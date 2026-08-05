from django.core.exceptions import ValidationError
from django.test import TestCase

from ..language_data import DEFAULT_LANGUAGES
from ..language_data import seed_languages
from ..models import Language


class DefaultLanguageDataTests(TestCase):
    def test_no_duplicate_names_or_codes(self):
        names = [name for name, _ in DEFAULT_LANGUAGES]
        codes = [code for _, code in DEFAULT_LANGUAGES]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(codes), len(set(codes)))

    def test_all_codes_are_two_or_three_lowercase_letters(self):
        for _, code in DEFAULT_LANGUAGES:
            self.assertRegex(code, r"^[a-z]{2,3}$")

    def test_covers_languages_taught_at_byu(self):
        codes = {code for _, code in DEFAULT_LANGUAGES}
        # A sample spanning CLS and the other language departments,
        # https://cls.byu.edu/language-classes
        for expected_code in [
            "en",
            "es",
            "fr",
            "it",
            "de",
            "pt",
            "ru",
            "ar",
            "zh",
            "ja",
            "ko",
            "he",
            "fa",
            "ase",
            "ceb",
            "haw",
            "hmn",
            "quc",
        ]:
            self.assertIn(expected_code, codes)

    def test_covers_languages_in_legacy_dump(self):
        # var/legacy_migration/legacy_dump.sqlite3's subtitles.language column
        # contains English, German, and Cakchiquel (Kaqchikel).
        names = {name for name, _ in DEFAULT_LANGUAGES}
        for expected_name in ["English", "German", "Cakchiquel"]:
            self.assertIn(expected_name, names)


class SeedLanguagesTests(TestCase):
    def test_post_migrate_hook_already_seeded_the_test_database(self):
        # The Django test runner creates the test database by running
        # migrations, which fires post_migrate and should have already
        # populated the Language table before this test ever runs.
        codes = set(Language.objects.values_list("bcp47", flat=True))
        for _, code in DEFAULT_LANGUAGES:
            self.assertIn(code, codes)

    def test_seed_languages_is_idempotent(self):
        before_count = Language.objects.count()
        seed_languages(Language)
        self.assertEqual(Language.objects.count(), before_count)

    def test_seed_languages_does_not_overwrite_renamed_language(self):
        english = Language.objects.get(bcp47="en")
        english.language = "Custom English Name"
        english.save()

        seed_languages(Language)

        english.refresh_from_db()
        self.assertEqual(english.language, "Custom English Name")

    def test_seed_languages_adds_missing_language(self):
        Language.objects.filter(bcp47="eo").delete()

        seed_languages(Language)

        self.assertTrue(
            Language.objects.filter(bcp47="eo", language="Esperanto").exists()
        )

    def test_seed_languages_skips_name_already_taken_by_a_different_code(self):
        # A defensive backstop: both `language` and `bcp47` are unique, so if
        # some pre-existing row's code doesn't match what DEFAULT_LANGUAGES
        # expects for that name (for whatever reason - manual DB edits, a
        # future data change, etc.), inserting a second row for the same
        # name would crash this post_migrate hook (and therefore `manage.py
        # migrate` itself) with an IntegrityError. It must skip instead.
        Language.objects.filter(bcp47="en").delete()
        Language.objects.create(language="English", bcp47="xx")

        seed_languages(Language)  # must not raise IntegrityError

        self.assertEqual(Language.objects.filter(language="English").count(), 1)
        self.assertEqual(Language.objects.get(language="English").bcp47, "xx")


class LanguageBcp47ValidationTests(TestCase):
    def test_rejects_codes_that_are_not_two_or_three_lowercase_letters(self):
        for invalid_code in ["e", "eng2", "EN", "e-g", ""]:
            language = Language(language="Test Language", bcp47=invalid_code)
            with self.assertRaises(ValidationError):
                language.full_clean()

    def test_accepts_a_valid_two_letter_code(self):
        language = Language(language="Test Language 2", bcp47="tz")
        language.full_clean()

    def test_accepts_a_valid_three_letter_code(self):
        language = Language(language="Test Language 3", bcp47="qaa")
        language.full_clean()
