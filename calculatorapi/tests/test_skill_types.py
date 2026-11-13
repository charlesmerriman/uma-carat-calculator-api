"""A skill's type for the Skills page's Type filter (calculatorapi/skill_types.py)."""

from django.test import SimpleTestCase

from calculatorapi.skill_types import FAMILIES, TYPE_LABELS, raises_own_current_speed, skill_type


def slot(*abilities):
    """One effect slot holding (type, target, value) abilities."""
    return {"abilities": [{"type": kind, "target_type": target, "value": value} for kind, target, value in abilities]}


class SkillTypeTests(SimpleTestCase):
    def test_a_green_is_typed_by_the_stat_it_raises(self):
        """Daptrius, 2026-11-12: the greens split into their sub-types, one icon family per stat."""
        self.assertEqual(
            [skill_type(icon) for icon in (10011, 10021, 10031, 10041, 10051, 10061)],
            ["stat_speed", "stat_stamina", "stat_power", "stat_guts", "stat_wit", "stat_several"],
        )

    def test_a_debuff_is_typed_by_the_harm_it_does(self):
        self.assertEqual(
            [skill_type(icon) for icon in (30011, 30021, 30041, 30051, 30071)],
            ["debuff_speed", "debuff_acceleration", "debuff_rushing", "debuff_stamina", "debuff_vision"],
        )
        # A debuff family with no skill in it yet is a debuff of no named kind.
        self.assertEqual(skill_type(30031), "debuff")

    def test_every_sub_type_sits_under_a_parent_the_filter_lists(self):
        for key in set(FAMILIES.values()) | {"current_speed", "unique", "other"}:
            self.assertIn(key, TYPE_LABELS)
            parent = key.split("_")[0]
            if "_" in key and key != "current_speed":
                self.assertIn(parent, TYPE_LABELS)

    def test_a_unique_an_undefined_skill_and_a_new_family(self):
        self.assertEqual(skill_type(0), "unique")
        self.assertEqual(skill_type(None), "other")
        self.assertEqual(skill_type(99991), "other")

    def test_a_speed_skill_that_raises_current_speed_is_its_own_type(self):
        velocity = [slot((27, 1, 3500))]
        current = [slot((22, 1, 3500))]
        both = [slot((27, 1, 1500), (21, 1, 1500))]
        self.assertEqual(skill_type(20011, velocity), "velocity")
        self.assertEqual(skill_type(20011, current), "current_speed")
        self.assertEqual(skill_type(20011, both), "current_speed")
        # With no effects to read (an announced skill), the icon decides.
        self.assertEqual(skill_type(20011), "velocity")

    def test_the_split_is_the_speed_groups_only(self):
        """A recovery or acceleration skill that also raises current speed keeps its family's type."""
        current = [slot((22, 1, 3500))]
        self.assertEqual(skill_type(20021, current), "recovery")
        self.assertEqual(skill_type(20041, current), "acceleration")

    def test_slowing_someone_else_is_not_raising_your_own(self):
        self.assertFalse(raises_own_current_speed([slot((21, 10, -1500))]))
        self.assertFalse(raises_own_current_speed([slot((21, 1, -1500))]))
        self.assertFalse(raises_own_current_speed(None))
        self.assertTrue(raises_own_current_speed([slot((27, 1, 100)), slot((22, 1, 100))]))
