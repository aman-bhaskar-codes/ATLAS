#!/usr/bin/env python3
"""Generate the first 50 Fibonacci numbers."""

def fibonacci(n):
    """Return a list of the first n Fibonacci numbers."""
    if n <= 0:
        return []
    seq = [0, 1]
    while len(seq) < n:
        seq.append(seq[-1] + seq[-2])
    return seq[:n]


def main():
    numbers = fibonacci(50)
    for i, value in enumerate(numbers):
        print(f"F({i}) = {value}")


if __name__ == "__main__":
    main()
