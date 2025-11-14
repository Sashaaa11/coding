#!/usr/bin/env python3
"""
scenario_runner.py — run chamber simulation from MeetingInfo.txt and write events to a .txt log.

Usage:
    python scenario_runner.py K MeetingInfo.txt --log output.txt

This variant logs plain messages (no timestamps) as you requested.
"""

from typing import Deque, Dict, List, Tuple
import threading
import time
import argparse
import sys
import os
from collections import deque

class ChamberState:
    def __init__(self, K: int, log_path: str):
        self.K = K
        self.current_type = None   # None, or 'S' / 'A' / 'O'
        self.count = 0
        self.waiting: Dict[str, int] = {'S': 0, 'A': 0, 'O': 0}
        # single lock for all condition variables
        self.lock = threading.Lock()
        self.cond: Dict[str, threading.Condition] = {
            'S': threading.Condition(self.lock),
            'A': threading.Condition(self.lock),
            'O': threading.Condition(self.lock),
        }
        # arrival queue: deque of (index, name, vtype)
        self.entry_order: Deque[Tuple[int, str, str]] = deque()

        self.log_path = log_path
        self._log_lock = threading.Lock()
        # ensure log directory exists
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_path)) or ".", exist_ok=True)
        except Exception:
            pass

        # start with a fresh run: overwrite log file
        try:
            with open(self.log_path, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass

    def log(self, msg: str):
        """Write plain message (no timestamp) to stdout and log file."""
        with self._log_lock:
            print(msg)
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(msg + "\n")
            except OSError as e:
                print(f"ERROR writing log file: {e}", file=sys.stderr)

    def remove_from_queue_by_index(self, index: int) -> bool:
        for i, (idx, name, vtype) in enumerate(self.entry_order):
            if idx == index:
                del self.entry_order[i]
                return True
        return False

def visitor(name: str, vtype: str, chamber: ChamberState, index: int):
    # register arrival in queue
    with chamber.lock:
        chamber.waiting[vtype] += 1
        chamber.entry_order.append((index, name, vtype))
        chamber.log(f"{name} ({'Student' if vtype=='S' else 'Admin' if vtype=='A' else 'Online'}) ARRIVED and queued.")

    if vtype in ('S', 'A'):
        # Students/Admins: can batch up to K
        cond = chamber.cond[vtype]
        with cond:
            while True:
                can_enter = False
                if chamber.current_type is None:
                    # chamber empty: only type at front may start
                    if chamber.entry_order:
                        _, _, front_type = chamber.entry_order[0]
                        if front_type == vtype:
                            # allowed to start a new in-person session
                            can_enter = True
                elif chamber.current_type == vtype and chamber.count < chamber.K:
                    # same-type session and space available
                    can_enter = True

                if can_enter:
                    chamber.count += 1
                    chamber.current_type = vtype
                    chamber.remove_from_queue_by_index(index)
                    chamber.waiting[vtype] -= 1
                    chamber.log(f"{name} ({'Student' if vtype=='S' else 'Admin'}) ENTERED the chamber. [count={chamber.count}]")
                    break
                # otherwise wait to be notified
                cond.wait()

        # simulate meeting duration (students/admins)
        time.sleep(0.5)

        with chamber.lock:
            chamber.count -= 1
            chamber.log(f"{name} ({'Student' if vtype=='S' else 'Admin'}) LEFT the chamber. [count={chamber.count}]")
            if chamber.count == 0:
                chamber.current_type = None
                # Notify all waiting types (they will re-evaluate, head-of-queue controls start)
                for t in ('O','S','A'):
                    if chamber.waiting[t] > 0:
                        with chamber.cond[t]:
                            chamber.cond[t].notify_all()
    else:
        # Online meeting: exclusive and one at a time
        cond = chamber.cond['O']
        with cond:
            while True:
                can_enter = False
                if chamber.current_type is None:
                    if chamber.entry_order:
                        _, _, front_type = chamber.entry_order[0]
                        if front_type == 'O':
                            can_enter = True
                if can_enter:
                    chamber.count = 1
                    chamber.current_type = 'O'
                    chamber.remove_from_queue_by_index(index)
                    chamber.waiting['O'] -= 1
                    chamber.log(f"{name} (Online Meeting) STARTED.")
                    break
                cond.wait()

        # simulate online meeting duration (exclusive)
        time.sleep(0.8)

        with chamber.lock:
            chamber.count = 0
            chamber.current_type = None
            chamber.log(f"{name} (Online Meeting) ENDED.")
            for t in ('O','S','A'):
                if chamber.waiting[t] > 0:
                    with chamber.cond[t]:
                        chamber.cond[t].notify_all()

def read_meeting_info(path: str) -> List[Tuple[str, str, float]]:
    """
    Parse MeetingInfo.txt lines into list of (name, type, delay).
    Accepts lines with tab or space separation. TYPE may be S, A, O.
    Default inter-arrival delay is 0.05s (small stagger to preserve order).
    """
    items: List[Tuple[str,str,float]] = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            s = ln.strip()
            if not s:
                continue
            # allow comments starting with #
            if s.startswith("#"):
                continue
            parts = s.split()
            # if the file is "NAME TYPE" or "NAME<TAB>TYPE"
            if len(parts) >= 2:
                name = parts[0]
                vtype = parts[1].upper()
                if vtype not in ('S','A','O'):
                    # try last char
                    vtype = vtype[-1]
                if vtype not in ('S','A','O'):
                    raise ValueError(f"Invalid type in line: {ln.strip()}")
                delay = 0.05
                items.append((name, vtype, delay))
    return items

def run_from_file(K: int, meeting_path: str, log_path: str):
    chamber = ChamberState(K, log_path)
    seq = read_meeting_info(meeting_path)
    threads: List[threading.Thread] = []
    for idx, (name, vtype, delay) in enumerate(seq, start=1):
        th = threading.Thread(target=visitor, args=(name, vtype, chamber, idx))
        threads.append(th)
        th.start()
        time.sleep(delay)
    for th in threads:
        th.join()
    chamber.log("Simulation finished.")

def parse_args(argv):
    p = argparse.ArgumentParser(description="Run chamber simulation from MeetingInfo.txt and write events to a .txt log.")
    p.add_argument("K", type=int, help="Capacity K for Students/Admin in an in-person session.")
    p.add_argument("meeting_file", help="Meeting info file (e.g., MeetingInfo.txt).")
    p.add_argument("--log", required=True, help="Output .txt log file path.")
    return p.parse_args(list(argv))

if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    run_from_file(args.K, args.meeting_file, args.log)