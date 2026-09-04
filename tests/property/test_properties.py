"""Properties every generated input must satisfy.

A unit test asks whether one input produces one output. A property asks what
is true of every input, which is a different question and finds different
bugs: the YAML divergences this project keeps rediscovering were all shapes
nobody thought to write a case for.

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
# safe-looking text is a generator that cannot find the bug where the dumper
# writes something the parity guard then refuses -- which is exactly what it
# missed the first time: `display: :0` came back out of PyYAML unquoted.
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
        """Round trip: dump then load returns what went in.

        This is the property that makes a saved config safe. It has to hold
        for every value the validator accepts, not just for the ones an
        example happens to use.
        """
        def prop(config):
            for backend in (False, True):
                if backend and not xtm.HAVE_PYYAML:
                    continue
                saved = xtm.HAVE_PYYAML
                xtm.HAVE_PYYAML = backend
                try:
                    text = xtm.dump_yaml(config)
                    assert xtm.load_yaml(text) == config, (
                        "%s backend: %r came back as %r"
                        % ("pyyaml" if backend else "built-in", config,
                           xtm.load_yaml(text)))
                finally:
                    xtm.HAVE_PYYAML = saved
            return True
        check(prop, a_config, runs=60)

    def test_what_xtm_writes_survives_its_own_guard(self):
        """The parity guard never refuses xtm's own output.

        A guard that rejected a file the tool had just written would make
        saving a config a way to break it.
        """
        def prop(config):
            xtm.dump_yaml(config)
            text = xtm.dump_yaml(config)
            xtm._scan_for_ambiguous_scalars(text)
            return True
        check(prop, a_config, runs=60)

    def test_both_backends_read_written_configs_identically(self):
        """The contract the whole guard exists for, over generated input."""
        if not xtm.HAVE_PYYAML:
            self.skipTest("PyYAML is not installed, so there is nothing to "
                          "compare the built-in parser against")

        def prop(config):
            text = xtm.dump_yaml(config)
            saved = xtm.HAVE_PYYAML
            try:
                xtm.HAVE_PYYAML = False
                builtin = xtm.load_yaml(text)
                xtm.HAVE_PYYAML = True
                pyyaml = xtm.load_yaml(text)
            finally:
                xtm.HAVE_PYYAML = saved
            assert builtin == pyyaml, ("built-in read %r, PyYAML read %r"
                                       % (builtin, pyyaml))
            return True
        check(prop, a_config, runs=60)


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

        The charset exists precisely so that names are safe as YAML keys
        under both backends; this is that claim, checked rather than
        asserted.
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

    def test_key_normalisation_is_idempotent(self):
        """Normalising twice is normalising once.

        `_normalize_config_keys` is what makes PyYAML's typed keys and the
        built-in reader's text agree, and it runs on every load.
        """
        def prop(config):
            once = {"profiles": dict(config["profiles"])}
            xtm._normalize_config_keys(once)
            twice = {"profiles": dict(once["profiles"])}
            xtm._normalize_config_keys(twice)
            assert once == twice
            return True
        check(prop, a_config, runs=60)


if __name__ == "__main__":
    unittest.main()
