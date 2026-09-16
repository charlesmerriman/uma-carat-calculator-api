"""The Django admin: pages render, content editors stay out of user data, and the image picker."""

import shutil
import tempfile
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.urls import NoReverseMatch, reverse

from calculatorapi import image_library
from calculatorapi.image_library import (
    image_prefixes,
    invalidate,
    is_valid_key,
    list_images,
    listing_is_cached,
    normalize_prefix,
)
from calculatorapi.management.commands.create_content_editor_group import CONTENT_MODELS
from calculatorapi.models import CustomUser, Uma, SocialAccount
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import make_user


# Smallest valid PNG (1x1, transparent). ImageField runs Pillow over uploads,
# so the upload-vs-library test needs real image bytes, not arbitrary content.
PNG_1PX = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
    b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class AdminSmokeTests(CalculatorTestCase):
    """Changelist and add pages render for a superuser.

    Catches admin config mistakes (bad list_display refs, broken fieldsets,
    autocomplete targets without search_fields) that only surface on render.
    """

    CONTENT_URL_NAMES = [
        'admin:calculatorapi_bannertimeline',
        'admin:calculatorapi_banneruma',
        'admin:calculatorapi_bannersupport',
        'admin:calculatorapi_bannerstepup',
        'admin:calculatorapi_uma',
        'admin:calculatorapi_supportcard',
        'admin:calculatorapi_skill',
        'admin:calculatorapi_gameevent',
        'admin:calculatorapi_championsmeeting',
        'admin:calculatorapi_leagueofheroes',
        'admin:calculatorapi_clubrank',
        'admin:calculatorapi_anniversaryevent',
        'admin:calculatorapi_scenario',
    ]

    @classmethod
    def setUpTestData(cls):
        cls.superuser = CustomUser.objects.create_superuser(
            username='boss', password='x')

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_changelists_render(self):
        for base in self.CONTENT_URL_NAMES:
            with self.subTest(url=f'{base}_changelist'):
                res = self.client.get(reverse(f'{base}_changelist'))
                self.assertEqual(res.status_code, 200)

    def test_add_forms_render(self):
        for base in self.CONTENT_URL_NAMES:
            with self.subTest(url=f'{base}_add'):
                res = self.client.get(reverse(f'{base}_add'))
                self.assertEqual(res.status_code, 200)

    def test_index_shows_friendly_names(self):
        res = self.client.get(reverse('admin:index'))
        self.assertContains(res, 'Uma Musume Data')      # app section heading
        self.assertContains(res, 'Uma banners')          # was "Banner umas"
        self.assertContains(res, 'League of Heroes events')
        self.assertNotContains(res, 'League of heroess')  # the old plural bug

    def test_join_models_not_registered_top_level(self):
        # Edited via inlines only — their changelists should not exist.
        for name in ['umasonumabanner', 'supportsonsupportbanner',
                     'championsmeetingumarecommendation', 'umaskill',
                     'supportcardskill']:
            with self.subTest(model=name):
                with self.assertRaises(NoReverseMatch):
                    reverse(f'admin:calculatorapi_{name}_changelist')


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class ContentEditorPermissionTests(CalculatorTestCase):
    """The "Content editors" group can manage content but never user data."""

    @classmethod
    def setUpTestData(cls):
        call_command('create_content_editor_group', stdout=StringIO())
        cls.editor = CustomUser.objects.create_user(
            username='editor', password='x', is_staff=True)
        cls.editor.groups.add(Group.objects.get(name='Content editors'))

    def setUp(self):
        self.client.force_login(self.editor)

    def test_editor_can_open_banner_changelist(self):
        res = self.client.get(reverse('admin:calculatorapi_banneruma_changelist'))
        self.assertEqual(res.status_code, 200)

    def test_editor_can_open_rank_changelist(self):
        res = self.client.get(reverse('admin:calculatorapi_clubrank_changelist'))
        self.assertEqual(res.status_code, 200)

    def test_editor_cannot_open_user_changelist(self):
        res = self.client.get(reverse('admin:calculatorapi_customuser_changelist'))
        self.assertEqual(res.status_code, 403)

    def test_editor_cannot_open_planned_banner_changelist(self):
        res = self.client.get(
            reverse('admin:calculatorapi_userplannedbanner_changelist'))
        self.assertEqual(res.status_code, 403)

    def test_editor_index_hides_user_models(self):
        res = self.client.get(reverse('admin:index'))
        self.assertNotContains(
            res, reverse('admin:calculatorapi_customuser_changelist'))
        self.assertNotContains(res, 'User planned banners')


class ContentEditorGroupCommandTests(CalculatorTestCase):
    """create_content_editor_group is idempotent and scoped to content only."""

    def test_command_is_idempotent(self):
        call_command('create_content_editor_group', stdout=StringIO())
        call_command('create_content_editor_group', stdout=StringIO())
        self.assertEqual(Group.objects.filter(name='Content editors').count(), 1)
        group = Group.objects.get(name='Content editors')
        # Derived from CONTENT_MODELS rather than hardcoded: the point of this
        # test is idempotency (running twice doesn't double up), not the size of
        # the list, and a literal here has to be hand-bumped for every new
        # content model.
        expected = len(CONTENT_MODELS) * 4  # add / change / delete / view
        self.assertEqual(group.permissions.count(), expected)

    def test_command_grants_no_user_data_permissions(self):
        call_command('create_content_editor_group', stdout=StringIO())
        codenames = set(
            Group.objects.get(name='Content editors')
            .permissions.values_list('codename', flat=True)
        )
        for forbidden in ['change_customuser', 'delete_customuser',
                          'change_userplannedbanner', 'view_userplannedbanner',
                          'change_token']:
            self.assertNotIn(forbidden, codenames)


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class SocialAccountAdminTests(CalculatorTestCase):
    """Linked accounts are visible but not editable, and the one identifying
    value we hold is never rendered."""

    def setUp(self):
        self.superuser = CustomUser.objects.create_superuser(
            username='root', password='x')
        self.client.force_login(self.superuser)
        self.link = SocialAccount.objects.create(
            user=make_user('user_abc123'), provider='google', subject_id='SECRET-SUB-999')

    def test_changelist_renders(self):
        res = self.client.get(reverse('admin:calculatorapi_socialaccount_changelist'))
        self.assertEqual(res.status_code, 200)

    def test_subject_id_is_not_exposed_in_changelist(self):
        res = self.client.get(reverse('admin:calculatorapi_socialaccount_changelist'))
        self.assertNotContains(res, 'SECRET-SUB-999')

    def test_subject_id_is_not_exposed_on_change_page(self):
        res = self.client.get(
            reverse('admin:calculatorapi_socialaccount_change', args=[self.link.pk]))
        self.assertNotContains(res, 'SECRET-SUB-999')

    def test_add_page_is_forbidden(self):
        res = self.client.get(reverse('admin:calculatorapi_socialaccount_add'))
        self.assertEqual(res.status_code, 403)

    def test_link_cannot_be_edited_via_post(self):
        other = make_user('user_victim')
        self.client.post(
            reverse('admin:calculatorapi_socialaccount_change', args=[self.link.pk]),
            {'user': other.pk, 'provider': 'discord', 'subject_id': 'HIJACK'})
        self.link.refresh_from_db()
        self.assertEqual(self.link.subject_id, 'SECRET-SUB-999')
        self.assertEqual(self.link.provider, 'google')

    def test_user_admin_no_longer_offers_email_or_name_fields(self):
        res = self.client.get(
            reverse('admin:calculatorapi_customuser_change', args=[self.superuser.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, 'name="email"')
        self.assertNotContains(res, 'name="first_name"')


class ImageLibraryTests(CalculatorTestCase):
    """The bucket-listing layer behind the admin's 'choose existing image' picker."""

    def test_prefixes_are_derived_from_every_image_field(self):
        self.assertEqual(
            image_prefixes(),
            frozenset({
                'umas/', 'support_cards/', 'banner_timelines/',
                'game_events/', 'champions_meetings/', 'league_of_heroes/',
                'anniversary_events/', 'step_up_banners/', 'scenarios/',
                'skills/',
            }),
        )

    def test_lists_only_image_files_sorted_by_name(self):
        listing = ([], ['b.png', 'notes.txt', 'A.jpg', 'c.webp'])
        with patch.object(image_library, 'default_storage') as storage:
            storage.listdir.return_value = listing
            storage.url.side_effect = lambda key: f'https://cdn.test/{key}'
            images = list_images('umas/')

        self.assertEqual([i['name'] for i in images], ['A.jpg', 'b.png', 'c.webp'])
        self.assertEqual(images[0]['key'], 'umas/A.jpg')
        self.assertEqual(images[0]['url'], 'https://cdn.test/umas/A.jpg')

    def test_second_call_is_served_from_cache(self):
        with patch.object(image_library, 'default_storage') as storage:
            storage.listdir.return_value = ([], ['a.png'])
            storage.url.return_value = 'https://cdn.test/umas/a.png'
            list_images('umas/')
            list_images('umas/')
            self.assertEqual(storage.listdir.call_count, 1)

    def test_bucket_failure_degrades_to_empty_and_is_not_cached(self):
        """A dead bucket must not 500 the admin, and must not poison the cache."""
        with patch.object(image_library, 'default_storage') as storage:
            storage.listdir.side_effect = OSError('no credentials')
            # assertLogs both asserts the failure is reported and keeps the
            # expected traceback out of the test runner's output.
            with self.assertLogs('calculatorapi.image_library', 'WARNING'):
                self.assertEqual(list_images('umas/'), [])
            self.assertFalse(listing_is_cached('umas/'))
            # A later call retries rather than serving the failure.
            self.assertEqual(storage.listdir.call_count, 1)
            with self.assertLogs('calculatorapi.image_library', 'WARNING'):
                list_images('umas/')
            self.assertEqual(storage.listdir.call_count, 2)

    def test_empty_folder_is_distinguishable_from_a_failed_listing(self):
        with patch.object(image_library, 'default_storage') as storage:
            storage.listdir.return_value = ([], [])
            self.assertEqual(list_images('umas/'), [])
            self.assertTrue(listing_is_cached('umas/'))

    def test_invalidate_forces_a_refetch(self):
        with patch.object(image_library, 'default_storage') as storage:
            storage.listdir.return_value = ([], ['a.png'])
            storage.url.return_value = 'https://cdn.test/umas/a.png'
            list_images('umas/')
            invalidate('umas/')
            list_images('umas/')
            self.assertEqual(storage.listdir.call_count, 2)

    def test_prefix_normalization_collapses_equivalent_spellings(self):
        self.assertEqual(normalize_prefix('umas'), 'umas/')
        self.assertEqual(normalize_prefix('/umas/'), 'umas/')
        self.assertEqual(normalize_prefix(''), '')
        self.assertEqual(normalize_prefix(None), '')

    def test_key_validation_rejects_anything_outside_the_image_folders(self):
        self.assertTrue(is_valid_key('umas/Special Week.png'))
        self.assertTrue(is_valid_key('support_cards/x.WEBP'))
        for bad in [
            '',                                # nothing picked
            '/umas/x.png',                     # absolute
            'umas/../../secrets/x.png',        # traversal
            'private/x.png',                   # folder not backing an ImageField
            'umas/notes.txt',                  # not an image
            'db.sqlite3',                      # bare path
        ]:
            with self.subTest(key=bad):
                self.assertFalse(is_valid_key(bad))


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class ImageLibraryEndpointTests(CalculatorTestCase):
    """/admin/image-library/ — staff-only, allow-listed folders."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = CustomUser.objects.create_superuser(username='boss', password='x')

    def setUp(self):
        self.url = reverse('admin-image-library')

    def test_anonymous_is_redirected_to_admin_login(self):
        res = self.client.get(self.url, {'prefix': 'umas/'})
        self.assertEqual(res.status_code, 302)
        self.assertIn('/admin/login/', res.url)

    def test_non_staff_user_is_redirected_not_served(self):
        self.client.force_login(make_user('regular'))
        res = self.client.get(self.url, {'prefix': 'umas/'})
        self.assertEqual(res.status_code, 302)
        self.assertIn('/admin/login/', res.url)

    def test_unknown_folder_is_rejected(self):
        self.client.force_login(self.superuser)
        for prefix in ['', 'private/', '../', 'staticfiles/']:
            with self.subTest(prefix=prefix):
                res = self.client.get(self.url, {'prefix': prefix})
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.json()['images'], [])

    def test_staff_gets_the_folder_listing(self):
        self.client.force_login(self.superuser)
        with patch.object(image_library, 'default_storage') as storage:
            storage.listdir.return_value = ([], ['a.png'])
            storage.url.return_value = 'https://cdn.test/umas/a.png'
            res = self.client.get(self.url, {'prefix': 'umas/'})

        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['prefix'], 'umas/')
        self.assertTrue(body['available'])
        self.assertEqual([i['key'] for i in body['images']], ['umas/a.png'])
        self.assertIn('support_cards/', body['folders'])

    def test_unreachable_bucket_reports_unavailable_rather_than_erroring(self):
        self.client.force_login(self.superuser)
        with patch.object(image_library, 'default_storage') as storage:
            storage.listdir.side_effect = OSError('boom')
            with self.assertLogs('calculatorapi.image_library', 'WARNING'):
                res = self.client.get(self.url, {'prefix': 'umas/'})

        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['available'])

    def test_refresh_bypasses_the_cache(self):
        self.client.force_login(self.superuser)
        with patch.object(image_library, 'default_storage') as storage:
            storage.listdir.return_value = ([], ['a.png'])
            storage.url.return_value = 'https://cdn.test/umas/a.png'
            self.client.get(self.url, {'prefix': 'umas/'})
            self.client.get(self.url, {'prefix': 'umas/'})
            self.assertEqual(storage.listdir.call_count, 1)
            self.client.get(self.url, {'prefix': 'umas/', 'refresh': '1'})
            self.assertEqual(storage.listdir.call_count, 2)


class SpacesImagePickerFormTests(CalculatorTestCase):
    """Saving a library choice writes the bucket key straight onto the field."""

    @classmethod
    def setUpClass(cls):
        # The upload test writes a real file through FileSystemStorage, and
        # MEDIA_ROOT is unset in settings (media lives in Spaces), which would
        # dump an "umas/" directory into backend/. Point it at a temp dir for
        # the duration and delete it afterwards.
        cls._media = tempfile.mkdtemp()
        cls._overrides = override_settings(
            STORAGES=PLAIN_TEST_STORAGES, MEDIA_ROOT=cls._media)
        cls._overrides.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._overrides.disable()
        shutil.rmtree(cls._media, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.superuser = CustomUser.objects.create_superuser(username='boss', password='x')

    # The uma page carries the UmaSkill inline, whose formset must be present
    # (empty) in every POST or the whole form is invalid and re-rendered.
    INLINE_FORMS = {
        'skills-TOTAL_FORMS': '0', 'skills-INITIAL_FORMS': '0',
        'skills-MIN_NUM_FORMS': '0', 'skills-MAX_NUM_FORMS': '1000',
    }

    def setUp(self):
        self.client.force_login(self.superuser)
        self.add_url = reverse('admin:calculatorapi_uma_add')

    def test_picked_key_is_saved_without_uploading_anything(self):
        res = self.client.post(self.add_url, {
            **self.INLINE_FORMS,
            'name': 'Special Week',
            'image': '',                                   # no upload
            'image-library-key': 'umas/Special Week.png',   # picked in the modal
        })
        self.assertEqual(res.status_code, 302)  # 302 == saved; 200 == redisplayed with errors
        self.assertEqual(Uma.objects.get(name='Special Week').image.name,
                         'umas/Special Week.png')

    def test_key_outside_the_library_is_rejected_with_a_form_error(self):
        res = self.client.post(self.add_url, {
            'name': 'Sneaky',
            'image': '',
            'image-library-key': '../../etc/passwd.png',
        })
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Uma.objects.filter(name='Sneaky').exists())
        self.assertContains(res, 'not in the media library')

    def test_an_upload_wins_over_a_simultaneous_library_pick(self):
        """Both submitted at once must keep the file the editor actually chose."""
        upload = SimpleUploadedFile('new.png', PNG_1PX, content_type='image/png')
        res = self.client.post(self.add_url, {
            **self.INLINE_FORMS,
            'name': 'Uploader',
            'image': upload,
            'image-library-key': 'umas/Some Other.png',
        })
        self.assertEqual(res.status_code, 302)
        saved = Uma.objects.get(name='Uploader').image.name
        self.assertNotEqual(saved, 'umas/Some Other.png')
        self.assertIn('new', saved)

    def test_no_pick_leaves_an_existing_image_untouched(self):
        uma = Uma.objects.create(name='Keeper', image='umas/Keeper.png')
        res = self.client.post(
            reverse('admin:calculatorapi_uma_change', args=[uma.pk]),
            {**self.INLINE_FORMS, 'name': 'Keeper renamed', 'image': '', 'image-library-key': ''})
        self.assertEqual(res.status_code, 302)
        uma.refresh_from_db()
        self.assertEqual(uma.name, 'Keeper renamed')
        self.assertEqual(uma.image.name, 'umas/Keeper.png')

    def test_change_form_renders_the_picker_for_every_image_admin(self):
        for base in ['bannertimeline', 'uma', 'supportcard', 'gameevent',
                     'championsmeeting', 'leagueofheroes']:
            with self.subTest(model=base):
                res = self.client.get(reverse(f'admin:calculatorapi_{base}_add'))
                self.assertEqual(res.status_code, 200)
                self.assertContains(res, 'spaces-picker')
                self.assertContains(res, 'name="image-library-key"')
                self.assertContains(res, 'spaces-image-picker.js')
