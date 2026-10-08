"""Oshis (favourite umas): one for everyone, how many a tier grants, what a lapse keeps,
and which umas may be picked."""

from io import StringIO

from django.core.management import call_command
from django.test import override_settings

from calculatorapi import benefits
from calculatorapi.models import OSHI_SLOT_CAP, PatreonSupporter, PatreonTier, Uma, UserOshi
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import auth_client, make_user


# FileSystemStorage rather than the Spaces backend: `uma.image.url` has to
# resolve without credentials. The tests compare against `uma.image.url`
# itself rather than a literal, so they hold whatever the storage's URL is.
@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class OshiTestCase(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes
    """Shared fixtures: the three prod tiers by their prod orders, and eight umas."""

    def setUp(self):
        # The orders are the live ones (GET /supporters, 2026-09-13). The
        # ladder in benefits.py is written against them.
        self.senior = PatreonTier.objects.create(name="Senior Class", order=1)
        self.classic = PatreonTier.objects.create(name="Classic Class", order=2)
        self.junior = PatreonTier.objects.create(name="Junior Class", order=3)
        self.user = make_user("user_a3f9c1")
        self.client, _ = auth_client(self.user)
        # Assigning a name to the ImageField is enough: nothing here opens the
        # file, and the storage is never asked to save one.
        # Eight: one more than the cap, so "past the cap" has something to send.
        self.umas = [
            Uma.objects.create(name=f"Uma {i}", image=f"umas/uma-{i}.png") for i in range(8)
        ]
        self.bare_uma = Uma.objects.create(name="No Picture")
        # The banner placeholder (never offered) and a costume variant
        # (offered, supporters and staff only).
        self.all_uma = Uma.objects.create(name="(All)", image="umas/all.png")
        self.outfit_uma = Uma.objects.create(name="Uma 0 (Summer)", image="umas/uma-0-summer.png")

    def _pledge(self, tier, user=None, **kwargs):
        return PatreonSupporter.objects.create(**{
            "display_name": "Rhondal",
            "patreon_user_id": "7",
            "tier": tier,
            "is_active": True,
            "linked_user": user or self.user,
            **kwargs,
        })

    def _get(self):
        return self.client.get("/account").json()

    def _patch(self, body):
        return self.client.patch("/account", body, format="json")

    def _ids(self, *indexes):
        return [self.umas[i].id for i in indexes]

    def _stored_ids(self):
        return list(self.user.oshis.order_by("position").values_list("uma_id", flat=True))


class OshiLadderTests(OshiTestCase):
    """benefits.oshi_slots: 1 free, then 3 / 5 / 7 by tier (the free slot plus 2, 4, 6)."""

    def test_free_account_has_one_slot(self):
        self.assertEqual(benefits.FREE_OSHI_SLOTS, 1)
        self.assertEqual(benefits.oshi_slots(self.user), 1)

    def test_costume_variants_are_for_any_paid_tier_and_staff(self):
        self.assertFalse(benefits.oshi_variants(self.user))
        self.assertFalse(benefits.oshi_variants(None))
        self.assertTrue(benefits.oshi_variants(make_user("staffer", is_staff=True)))
        self._pledge(self.junior)
        self.assertTrue(benefits.oshi_variants(self.user))

    def test_a_lapse_takes_costume_variants_away(self):
        self._pledge(self.junior, is_active=False)
        self.assertFalse(benefits.oshi_variants(self.user))

    def test_nobody_has_no_slots(self):
        self.assertEqual(benefits.oshi_slots(None), 0)

    def test_junior_gets_three(self):
        self._pledge(self.junior)
        self.assertEqual(benefits.oshi_slots(self.user), 3)

    def test_classic_gets_five(self):
        self._pledge(self.classic)
        self.assertEqual(benefits.oshi_slots(self.user), 5)

    def test_senior_gets_seven(self):
        self._pledge(self.senior)
        self.assertEqual(benefits.oshi_slots(self.user), 7)

    def test_a_tier_added_below_junior_gets_three(self):
        """The bottom rung is ANY_PAID_TIER, so a new lowest tier is covered."""
        trial = PatreonTier.objects.create(name="Debut", order=9)
        self._pledge(trial)
        self.assertEqual(benefits.oshi_slots(self.user), 3)

    def test_a_lapsed_pledge_keeps_the_free_slot(self):
        self._pledge(self.senior, is_active=False)
        self.assertEqual(benefits.oshi_slots(self.user), 1)

    def test_the_ladder_never_exceeds_the_model_cap(self):
        self.assertEqual(max(slots for _, slots in benefits.OSHI_SLOT_LADDER), OSHI_SLOT_CAP)

    def test_oshi_is_a_benefit_key_on_any_paid_tier(self):
        self._pledge(self.junior)
        self.assertTrue(benefits.has_benefit(self.user, benefits.OSHI))
        self.assertIn(benefits.OSHI, self._get()["supporter"]["benefits"])

    def test_staff_get_full_slots_with_no_pledge(self):
        staff = make_user("staffer", is_staff=True)
        self.assertEqual(benefits.oshi_slots(staff), OSHI_SLOT_CAP)

    def test_staff_slots_are_not_reduced_by_a_lower_tier(self):
        """Staff is a bypass, not a rung: a junior pledge does not cap them at 3."""
        staff = make_user("staffer", is_staff=True)
        self._pledge(self.junior, user=staff, patreon_user_id="8")
        self.assertEqual(benefits.oshi_slots(staff), OSHI_SLOT_CAP)


class OshiPictureTests(OshiTestCase):
    """GET /account: the first oshi is the picture, for every account."""

    def test_free_account_has_one_slot_and_no_picture_until_it_picks(self):
        body = self._get()
        self.assertIsNone(body["avatar_url"])
        self.assertEqual(body["oshi_slots"], 1)
        self.assertFalse(body["oshi_variants"])
        self.assertEqual(body["oshis"], [])

    def test_a_supporter_and_staff_are_told_they_may_pick_variants(self):
        self._pledge(self.junior)
        self.assertTrue(self._get()["oshi_variants"])
        client, _ = auth_client(make_user("staffer", is_staff=True))
        self.assertTrue(client.get("/account").json()["oshi_variants"])

    def test_a_free_account_s_pick_is_its_picture(self):
        self._patch({"oshis": self._ids(3)})
        body = self._get()
        self.assertEqual(body["avatar_url"], self.umas[3].image.url)
        self.assertEqual(body["oshi_slots"], 1)

    def test_the_first_oshi_is_the_picture(self):
        self._pledge(self.junior)
        self._patch({"oshis": self._ids(2, 0, 1)})

        body = self._get()

        self.assertEqual(body["avatar_url"], self.umas[2].image.url)
        self.assertEqual(body["oshi_slots"], 3)
        self.assertEqual(
            body["oshis"],
            [
                {"position": 0, "id": self.umas[2].id, "name": "Uma 2", "image": self.umas[2].image.url},
                {"position": 1, "id": self.umas[0].id, "name": "Uma 0", "image": self.umas[0].image.url},
                {"position": 2, "id": self.umas[1].id, "name": "Uma 1", "image": self.umas[1].image.url},
            ],
        )

    def test_reordering_changes_the_picture(self):
        self._pledge(self.classic)
        self._patch({"oshis": self._ids(0, 1)})

        response = self._patch({"oshis": self._ids(1, 0)})

        self.assertEqual(response.json()["avatar_url"], self.umas[1].image.url)

    def test_a_lapse_keeps_the_rows_and_the_picture(self):
        """The Dave rule: nothing is deleted. The free slot keeps the first one covered."""
        pledge = self._pledge(self.senior)
        self._patch({"oshis": self._ids(0, 1, 2, 3, 4, 5, 6)})
        pledge.is_active = False
        pledge.save()

        body = self._get()

        self.assertEqual(body["avatar_url"], self.umas[0].image.url)
        self.assertEqual(body["oshi_slots"], 1)
        self.assertEqual([row["id"] for row in body["oshis"]], self._ids(0, 1, 2, 3, 4, 5, 6))

    def test_a_downgrade_keeps_every_row_and_reports_the_smaller_count(self):
        pledge = self._pledge(self.senior)
        self._patch({"oshis": self._ids(0, 1, 2, 3, 4, 5, 6)})
        pledge.tier = self.classic
        pledge.save()

        body = self._get()

        self.assertEqual(body["avatar_url"], self.umas[0].image.url)
        self.assertEqual(body["oshi_slots"], 5)
        self.assertEqual(len(body["oshis"]), 7)

    def test_deleting_an_uma_removes_that_oshi_and_the_next_becomes_the_picture(self):
        self._pledge(self.classic)
        self._patch({"oshis": self._ids(0, 1)})
        self.umas[0].delete()

        body = self._get()

        self.assertEqual([row["id"] for row in body["oshis"]], self._ids(1))
        self.assertEqual(body["avatar_url"], self.umas[1].image.url)

    def test_the_picture_is_the_borderless_art_when_the_uma_has_it(self):
        self._pledge(self.classic)
        self._patch({"oshis": self._ids(0, 1)})
        self.umas[0].image_borderless = "umas_borderless/uma-0.png"
        self.umas[0].save()

        body = self._get()

        self.assertEqual(body["avatar_url"], self.umas[0].image_borderless.url)
        self.assertEqual(body["oshis"][0]["image"], self.umas[0].image_borderless.url)
        # No borderless file yet: the bordered art, as before.
        self.assertEqual(body["oshis"][1]["image"], self.umas[1].image.url)

    def test_an_uma_whose_picture_was_cleared_yields_to_the_next(self):
        # An editor can clear the image after the pick was made. The row stays
        # (the pick is still theirs) with image "", and the picture falls
        # through to the next oshi rather than to a broken tile.
        self._pledge(self.classic)
        self._patch({"oshis": self._ids(0, 1)})
        self.umas[0].image = None
        self.umas[0].save()

        body = self._get()

        self.assertEqual(body["oshis"][0]["image"], "")
        self.assertEqual(body["avatar_url"], self.umas[1].image.url)

    def test_deleting_the_account_deletes_its_oshis(self):
        self._pledge(self.classic)
        self._patch({"oshis": self._ids(0)})
        self.client.delete("/account")
        self.assertFalse(UserOshi.objects.exists())

    def test_the_purge_leaves_oshis_alone(self):
        self._pledge(self.classic)
        self._patch({"oshis": self._ids(0, 1)})
        call_command("purge_user_pii", "--no-input", stdout=StringIO())
        self.assertEqual(self._stored_ids(), self._ids(0, 1))

    def test_staff_have_full_oshi_access_with_no_pledge(self):
        """Staff unlock the slot count without becoming a Patreon supporter."""
        staff = make_user("staffer", is_staff=True)
        client, _ = auth_client(staff)

        get_body = client.get("/account").json()
        self.assertEqual(get_body["oshi_slots"], OSHI_SLOT_CAP)
        self.assertFalse(get_body["supporter"]["is_supporter"])

        patch_body = client.patch(
            "/account", {"oshis": self._ids(0, 1, 2, 3, 4, 5, 6)}, format="json"
        ).json()
        self.assertEqual(patch_body["avatar_url"], self.umas[0].image.url)
        self.assertEqual(len(patch_body["oshis"]), 7)


class OshiWriteTests(OshiTestCase):  # pylint: disable=too-many-public-methods
    """PATCH /account {"oshis": [...]}: what may be added, and what may always be done."""

    def test_a_free_account_may_pick_one(self):
        response = self._patch({"oshis": self._ids(0)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored_ids(), self._ids(0))

    def test_a_free_account_may_not_pick_two(self):
        response = self._patch({"oshis": self._ids(0, 1)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["oshis"][0],
            "Free accounts get one favourite. Patreon supporters get more.",
        )
        self.assertEqual(self._stored_ids(), [])

    def test_a_free_account_may_swap_its_one(self):
        self._patch({"oshis": self._ids(0)})
        response = self._patch({"oshis": self._ids(1)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored_ids(), self._ids(1))

    def test_a_supporter_may_fill_their_slots(self):
        self._pledge(self.junior)
        response = self._patch({"oshis": self._ids(3, 4, 5)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored_ids(), self._ids(3, 4, 5))

    def test_one_past_the_slot_count_is_refused(self):
        self._pledge(self.junior)
        response = self._patch({"oshis": self._ids(0, 1, 2, 3)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["oshis"][0], "Your tier covers 3 favourites.")
        self.assertEqual(self._stored_ids(), [])

    def test_the_message_names_the_tier_s_count(self):
        self._pledge(self.classic)
        response = self._patch({"oshis": self._ids(0, 1, 2, 3, 4, 5)})
        self.assertEqual(response.json()["oshis"][0], "Your tier covers 5 favourites.")

    def test_more_than_the_cap_is_refused_even_for_the_top_tier(self):
        self._pledge(self.senior)
        response = self._patch({"oshis": self._ids(0, 1, 2, 3, 4, 5, 6, 7)})
        self.assertEqual(response.status_code, 400)
        self.assertIn("oshis", response.json())

    # the picker's rules: Uma.pickable, and costume variants for supporters ──

    def test_the_all_placeholder_is_refused_for_everyone(self):
        self._pledge(self.senior)
        response = self._patch({"oshis": [self.all_uma.id]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["oshis"][0], "That one can't be picked.")
        self.assertEqual(self._stored_ids(), [])

    def test_a_free_account_may_not_add_a_costume_variant(self):
        response = self._patch({"oshis": [self.outfit_uma.id]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["oshis"][0], "Costume variants are a Patreon supporter perk."
        )
        self.assertEqual(self._stored_ids(), [])

    def test_a_supporter_may_add_a_costume_variant(self):
        self._pledge(self.junior)
        response = self._patch({"oshis": self._ids(0) + [self.outfit_uma.id]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored_ids(), self._ids(0) + [self.outfit_uma.id])

    def test_staff_may_add_a_costume_variant_with_no_pledge(self):
        client, _ = auth_client(make_user("staffer", is_staff=True))
        response = client.patch("/account", {"oshis": [self.outfit_uma.id]}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_a_lapsed_supporter_keeps_a_held_variant_and_may_reorder_it(self):
        """The variant was theirs to add; a lapse never refuses a keep, reorder or trim."""
        pledge = self._pledge(self.junior)
        self._patch({"oshis": [self.outfit_uma.id] + self._ids(1)})
        pledge.is_active = False
        pledge.save()

        response = self._patch({"oshis": self._ids(1) + [self.outfit_uma.id]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored_ids(), self._ids(1) + [self.outfit_uma.id])
        # ...but swapping in a second variant is an ADD, and refused.
        second = Uma.objects.create(name="Uma 1 (Winter)", image="umas/uma-1-winter.png")
        response = self._patch({"oshis": [second.id, self.outfit_uma.id]})
        self.assertEqual(response.status_code, 400)

    def test_pickable_is_what_the_picker_lists(self):
        self.assertEqual(
            set(Uma.pickable().values_list("name", flat=True)),
            {f"Uma {i}" for i in range(8)} | {"Uma 0 (Summer)"},
        )
        self.assertTrue(self.outfit_uma.is_costume_variant)
        self.assertFalse(self.umas[0].is_costume_variant)

    def test_the_same_uma_twice_is_refused(self):
        self._pledge(self.classic)
        response = self._patch({"oshis": self._ids(0, 0)})
        self.assertEqual(response.status_code, 400)
        self.assertIn("twice", response.json()["oshis"][0])

    def test_an_uma_without_a_picture_is_refused(self):
        self._pledge(self.junior)
        response = self._patch({"oshis": [self.bare_uma.id]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("oshis", response.json())

    def test_an_unknown_uma_is_refused(self):
        self._pledge(self.junior)
        response = self._patch({"oshis": [999_999]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("oshis", response.json())

    def test_a_replace_renumbers_from_zero(self):
        self._pledge(self.junior)
        self._patch({"oshis": self._ids(0, 1, 2)})
        self._patch({"oshis": self._ids(2)})
        self.assertEqual(
            list(self.user.oshis.values_list("position", "uma_id")), [(0, self.umas[2].id)]
        )

    def test_an_empty_list_clears_them(self):
        self._patch({"oshis": self._ids(0)})
        response = self._patch({"oshis": []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored_ids(), [])
        self.assertIsNone(response.json()["avatar_url"])

    def test_a_patch_without_oshis_leaves_them_alone(self):
        self._patch({"oshis": self._ids(0)})
        self._patch({"display_name": "Rhondal"})
        self.assertEqual(self._stored_ids(), self._ids(0))

    # the lapse rule: keep, reorder and remove are always allowed; add is not ──

    def test_a_downgraded_supporter_may_reorder_what_they_hold(self):
        pledge = self._pledge(self.senior)
        self._patch({"oshis": self._ids(0, 1, 2, 3, 4, 5, 6)})
        pledge.tier = self.classic
        pledge.save()

        response = self._patch({"oshis": self._ids(6, 5, 4, 3, 2, 1, 0)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored_ids(), self._ids(6, 5, 4, 3, 2, 1, 0))
        self.assertEqual(response.json()["avatar_url"], self.umas[6].image.url)

    def test_a_downgraded_supporter_may_remove_down_to_their_count(self):
        pledge = self._pledge(self.senior)
        self._patch({"oshis": self._ids(0, 1, 2, 3, 4, 5, 6)})
        pledge.tier = self.classic
        pledge.save()

        response = self._patch({"oshis": self._ids(0, 1, 2, 3, 4, 5)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored_ids(), self._ids(0, 1, 2, 3, 4, 5))

    def test_a_downgraded_supporter_may_not_swap_in_a_new_one(self):
        """Seven rows, five slots: replacing one of the seven is an ADD."""
        pledge = self._pledge(self.senior)
        self._patch({"oshis": self._ids(0, 1, 2, 3, 4, 5, 6)})
        pledge.tier = self.classic
        pledge.save()

        response = self._patch({"oshis": self._ids(0, 1, 2, 3, 4, 5, 7)})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._stored_ids(), self._ids(0, 1, 2, 3, 4, 5, 6))

    def test_a_downgraded_supporter_may_pick_fresh_within_their_count(self):
        pledge = self._pledge(self.senior)
        self._patch({"oshis": self._ids(0, 1, 2, 3, 4, 5, 6)})
        pledge.tier = self.classic
        pledge.save()

        response = self._patch({"oshis": self._ids(7, 6, 5, 4, 3)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored_ids(), self._ids(7, 6, 5, 4, 3))

    def test_a_lapsed_supporter_may_clear_or_trim_but_not_add(self):
        """Three rows, one (free) slot left: keep, reorder and trim, never add."""
        pledge = self._pledge(self.junior)
        self._patch({"oshis": self._ids(0, 1, 2)})
        pledge.is_active = False
        pledge.save()

        self.assertEqual(self._patch({"oshis": self._ids(0, 1, 2, 3)}).status_code, 400)
        self.assertEqual(self._patch({"oshis": self._ids(2, 1, 0)}).status_code, 200)
        self.assertEqual(self._patch({"oshis": self._ids(2)}).status_code, 200)
        self.assertEqual(self._patch({"oshis": []}).status_code, 200)
        self.assertEqual(self._stored_ids(), [])

    def test_a_lapsed_supporter_s_refusal_reads_as_the_free_message(self):
        pledge = self._pledge(self.junior)
        self._patch({"oshis": self._ids(0)})
        pledge.is_active = False
        pledge.save()

        response = self._patch({"oshis": self._ids(0, 1)})

        self.assertEqual(response.json()["oshis"][0],
                         "Free accounts get one favourite. Patreon supporters get more.")

    def test_oshis_are_per_account(self):
        other = make_user("user_b7e2d0")
        other_client, _ = auth_client(other)
        self._patch({"oshis": self._ids(0)})
        other_client.patch("/account", {"oshis": self._ids(0)}, format="json")

        self.assertEqual(self._stored_ids(), self._ids(0))
        self.assertEqual(list(other.oshis.values_list("uma_id", flat=True)), self._ids(0))
        self.assertEqual(UserOshi.objects.count(), 2)
