"""A very small property-testing engine: generate, check, shrink.

Hypothesis would be the obvious choice and is deliberately not used. xtm's
premise is that it runs where nothing can be installed, and the suite follows
the tool: every other test here runs with the standard library alone, on the
Python the machine happens to have. A property test nobody can run because
the dependency is missing tests nothing.

What this gives up against Hypothesis is real shrinking strategy, a corpus
and statistics. What it keeps is the part that finds bugs: many generated
inputs per property, a fixed set of seeds so a failure is reproducible, and
enough shrinking to report a small counterexample rather than a random one.
"""

import random


class Falsified(AssertionError):
    """A property did not hold, with the smallest input found."""


def check(prop, generator, seeds=(1, 7, 1234), runs=200, shrinks=200):
    """Run `prop` over generated values; raise Falsified on the first failure.

    `prop` takes one generated value and raises AssertionError (or returns
    False) when the property does not hold. `generator` takes a Random and
    returns one value.
    """
    for seed in seeds:
        rng = random.Random(seed)
        for _ in range(runs):
            value = generator(rng)
            error = _failure(prop, value)
            if error is None:
                continue
            smaller, smaller_error = _shrink(prop, value, rng, shrinks)
            raise Falsified(
                "seed %d: property failed for %r: %s" % (seed, smaller,
                                                         smaller_error or error))


def _failure(prop, value):
    """The failure message for one value, or None when the property holds."""
    try:
        result = prop(value)
    except AssertionError as e:
        return str(e) or "assertion failed"
    if result is False:
        return "returned False"
    return None


def _shrink(prop, value, rng, budget):
    """Repeatedly try simpler values that still fail.

    Simplification is deliberately crude -- shorter strings, smaller
    numbers, fewer keys -- because its only job is to make the reported
    counterexample readable.
    """
    best, best_error = value, _failure(prop, value)
    for _ in range(budget):
        candidates = list(_simpler(best, rng))
        for candidate in candidates:
            error = _failure(prop, candidate)
            if error is not None:
                best, best_error = candidate, error
                break
        else:
            break
    return best, best_error


def _simpler(value, rng):
    """Candidate simplifications of one value."""
    if isinstance(value, str) and value:
        yield value[:-1]
        yield value[1:]
    elif isinstance(value, bool):
        return
    elif isinstance(value, int):
        if value not in (0, 1):
            yield value // 2
            yield 0
    elif isinstance(value, dict) and value:
        for key in list(value):
            smaller = dict(value)
            del smaller[key]
            yield smaller
        for key, inner in list(value.items()):
            for candidate in _simpler(inner, rng):
                yield dict(value, **{key: candidate})
    elif isinstance(value, list) and value:
        yield value[:-1]
        yield value[1:]
