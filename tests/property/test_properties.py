"""Properties every generated input must satisfy.

A unit test asks whether one input produces one output. A property asks what
is true of every input, which is a different question and finds different
bugs: the config-writing defects this project found were all shapes nobody
thought to write a case for.

Each property is stated once, in a name, and checked over generated inputs
from fixed seeds. A failure reports a shrunk counterexample and is
reproducible from the seed in the message.
"""

import string
import unittest

from tests.property.engine import check
from tests.support import xtm

NAME_CHARS = string.ascii_letters + string.digits + "._-"
# Deliberately includes YAML's structural characters. A generator limited to
# safe-looking text is a generator that cannot find the value the writer
# forgets to quote.
SCALAR_CHARS = string.ascii_letters + string.digits + " ._-/@%+=~:#*&!|>,[]{}'\""


def a_name(rng, minimum=1, maximum=12):
    """A session or profile name from the documented charset."""
    return "".join(rng.choice(NAME_CHARS)
                   for _ in range(rng.randint(minimum, maximum)))


def a_scalar(rng):
    """A plain value a user might reasonably write unquoted."""
    return "".join(rng.choice(SCALAR_CHARS)
                   for _ in range(rng.randint(1, 20))).strip() or "x"


def a_geometry(rng):
    return {"x": rng.randint(-4000, 4000), "y": rng.randint(-4000, 4000),
            "width": rng.randint(1, 4000), "height": rng.randint(1, 4000)}


def a_profile(rng):
    """A profile the validator should accept."""
    profile = {"sessions": {}}
    if rng.random() < 0.5:
        profile["prefix"] = a_name(rng, 1, 5) + "-"
    if rng.random() < 0.3:
        profile["on_switch"] = rng.choice(["leave", "detach", "kill"])
    for _ in range(rng.randint(0, 4)):
        slot = dict(a_geometry(rng))
        if rng.random() < 0.3:
            slot["command"] = a_scalar(rng)
        if rng.random() < 0.3:
            slot["cwd"] = "/" + a_name(rng)
        if rng.random() < 0.2:
            slot["xterm_args"] = ["-fn", a_name(rng)]
        profile["sessions"][a_name(rng)] = slot
    return profile


def a_config(rng):
    return {"profiles": {a_name(rng): a_profile(rng)
                         for _ in range(rng.randint(1, 3))}}


class TestYamlProperties(unittest.TestCase):
    """What must be true of every config the tool writes."""

    def test_anything_xtm_writes_it_can_read_back(self):
        """Round trip: dump then load returns what went in, in order.

        This is the property that makes a saved config safe. It has to hold
        for every value the validator accepts, not just for the ones an
        example happens to use.
        """
        def prop(config):
            back = xtm.load_yaml(xtm.dump_yaml(config))
            assert back == config, "%r came back as %r" % (config, back)
            for name, profile in config["profiles"].items():
                assert (list(back["profiles"][name]["sessions"])
                        == list(profile["sessions"])), "slot order changed"
            return True
        check(prop, a_config, runs=60)

    def test_a_typed_looking_scalar_survives_as_text(self):
        """Text YAML would read as a boolean, number or date is quoted."""
        words = ["yes", "No", "on", "OFF", "~", "null", "0755", "1:30",
                 "1_000", "2026-01-01", ".inf", "0x1f", "1e3", "true"]

        def prop(value):
            config = {"a": {"command": value, "title": value}}
            assert xtm.load_yaml(xtm.dump_yaml(config)) == config
            return True
        check(prop, lambda rng: rng.choice(words + [a_scalar(rng)]))


class TestGeometryProperties(unittest.TestCase):

    def test_parse_inverts_the_comma_format(self):
        """`--set` accepts exactly what the geometry format produces."""
        def prop(geometry):
            spec = "%d,%d,%d,%d" % (geometry["x"], geometry["y"],
                                    geometry["width"], geometry["height"])
            assert xtm.parse_geometry_spec(spec) == geometry
            return True
        check(prop, a_geometry)

    def test_stack_offsets_are_monotonic_in_the_index(self):
        """Consecutive stacked windows never move backwards.

        Stacking is the one placement path with arithmetic in it, and the
        arithmetic is what a reader would have to re-derive.
        """
        def prop(case):
            stack, index = case
            here = xtm.stack_slot(stack, index)
            later = xtm.stack_slot(stack, index + 1)
            assert later["x"] - here["x"] == stack["offset_x"]
            assert later["y"] - here["y"] == stack["offset_y"]
            assert here["width"] == stack["width"]
            assert here["height"] == stack["height"]
            return True

        def generator(rng):
            stack = dict(a_geometry(rng),
                         offset_x=rng.randint(0, 200),
                         offset_y=rng.randint(0, 200))
            return (stack, rng.randint(0, 50))
        check(prop, generator)


class TestNamingProperties(unittest.TestCase):

    def test_a_valid_name_survives_a_round_trip_as_a_key(self):
        """Any name the validator accepts can be stored and read back.

        The charset exists precisely so that names are plain YAML keys; this
        is that claim, checked rather than asserted.
        """
        def prop(name):
            config = {"profiles": {name: {"sessions": {}}}}
            text = xtm.dump_yaml(config)
            assert list(xtm.load_yaml(text)["profiles"]) == [name]
            return True

        def generator(rng):
            name = a_name(rng)
            while not xtm.is_valid_session_name(name):
                name = a_name(rng)
            return name
        check(prop, generator)

    def test_an_explicit_session_is_never_prefixed(self):
        """Ownership in one line: `session:` wins over `prefix:`, always."""
        def prop(case):
            prefix, slot, session = case
            profile = {"prefix": prefix,
                       "sessions": {slot: {"session": session}}}
            assert xtm.resolve_session_name(profile, slot) == session
            bare = {"prefix": prefix, "sessions": {slot: {}}}
            assert xtm.resolve_session_name(bare, slot) == prefix + slot
            return True

        def generator(rng):
            return (a_name(rng, 1, 5) + "-", a_name(rng), a_name(rng))
        check(prop, generator)


if __name__ == "__main__":
    unittest.main()
