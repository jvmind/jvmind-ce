#!/usr/bin/env python3
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from react_agent.gc_analyzer import parse_gc_log, compute_stats

# Read the test log
log_path = os.path.join(os.path.dirname(__file__), 'gc-jdk11-zgc-full.log')
with open(log_path, 'r') as f:
    lines = f.readlines()

# Parse it
result = parse_gc_log('\n'.join(lines))

print(f"Collector: {result['collector']}")
print(f"Total events: {len(result['events'])}")

# Count Full GC events and check causes
full_count = 0
system_gc_count = 0
for e in result['events']:
    if e.category == 'Full':
        full_count += 1
        print(f"  Full GC: cause={repr(e.cause)}")
        if e.cause == 'System.gc()':
            system_gc_count += 1

print(f"\nFull GC events: {full_count}")
print(f"System.gc() Full GC: {system_gc_count}")

# Check statistics
stats = compute_stats(result)
print(f"\nby_cause_full: {list(stats['by_cause_full'].keys())}")
if 'by_cause_full' in stats:
    for cause, data in stats['by_cause_full'].items():
        print(f"  {cause}: count={data['count']} total_pause={data['total_pause_ms']}ms")
