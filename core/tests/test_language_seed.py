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

    def test_all_codes_are_three_lowercase_letters(self):
        for _, code in DEFAULT_LANGUAGES:
            self.assertRegex(code, r"^[a-z]{3}$")

    def test_covers_languages_taught_at_byu(self):
        codes = {code for _, code in DEFAULT_LANGUAGES}
        # A sample spanning CLS and the other language departments,
        # https://cls.byu.edu/language-classes
        for expected_code in [
            "eng",
            "spa",
            "fra",
            "ita",
            "deu",
            "por",
            "rus",
            "ara",
            "zho",
            "jpn",
            "kor",
            "heb",
            "fas",
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
        codes = set(Language.objects.values_list("iso_639_3", flat=True))
        for _, code in DEFAULT_LANGUAGES:
            self.assertIn(code, codes)

    def test_seed_languages_is_idempotent(self):
        before_count = Language.objects.count()
        seed_languages(Language)
        self.assertEqual(Language.objects.count(), before_count)

    def test_seed_languages_does_not_overwrite_renamed_language(self):
        english = Language.objects.get(iso_639_3="eng")
        english.language = "Custom English Name"
        english.save()

        seed_languages(Language)

        english.refresh_from_db()
        self.assertEqual(english.language, "Custom English Name")

    def test_seed_languages_adds_missing_language(self):
        Language.objects.filter(iso_639_3="epo").delete()

        seed_languages(Language)

        self.assertTrue(
            Language.objects.filter(iso_639_3="epo", language="Esperanto").exists()
        )


class LanguageIso6393ValidationTests(TestCase):
    def test_rejects_codes_that_are_not_three_lowercase_letters(self):
        for invalid_code in ["en", "eng2", "ENG", "e-g", ""]:
            language = Language(language="Test Language", iso_639_3=invalid_code)
            with self.assertRaises(ValidationError):
                language.full_clean()

    def test_accepts_a_valid_three_letter_code(self):
        language = Language(language="Test Language", iso_639_3="qaa")
        language.full_clean()
