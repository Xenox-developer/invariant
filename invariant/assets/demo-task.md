Implement merge_windows(windows) in /workspace/solution.py.

This is a small calendar utility. Windows are half-open integer intervals [start, end).
The input must be a list or tuple of pairs; each pair must be a list or tuple of
exactly two integers. Booleans are not integers for this API. Reject any invalid
input or a pair with start > end by raising ValueError. Negative endpoints are valid.

Ignore zero-length intervals. Sort the remaining intervals and merge both overlapping
and touching intervals. Return a list of two-element lists. Do not mutate the input.

Examples:
  [[5, 8], [1, 3], [3, 6]] -> [[1, 8]]
  [[1, 2], [4, 5]] -> [[1, 2], [4, 5]]
  [[2, 2]] -> []

Change only solution.py. Use Python's standard library; no installation or networking
is needed. You may run your own checks with python3 -B. The same captured evaluator
will assess every configuration independently after you finish. Implement the function
and finish; do not request a different environment or additional agent tools.
