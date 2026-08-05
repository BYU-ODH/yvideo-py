from django.test import TestCase

from ..language_data import DEFAULT_LANGUAGES
from ..language_data import seed_languages
from ..models import Language


class DefaultLanguageDataTests(TestCase):
    def test_no_duplicate_names_or_tags(self):
        names = [name for name, _ in DEFAULT_LANGUAGES]
        tags = [tag for _, tag in DEFAULT_LANGUAGES]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(tags), len(set(tags)))

    def test_covers_languages_taught_at_byu(self):
        tags = {tag for _, tag in DEFAULT_LANGUAGES}
        # A sample spanning CLS and the other language departments,
        # https://cls.byu.edu/language-classes
        for expected_tag in [
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
            self.assertIn(expected_tag, tags)


class SeedLanguagesTests(TestCase):
    def test_post_migrate_hook_already_seeded_the_test_database(self):
        # The Django test runner creates the test database by running
        # migrations, which fires post_migrate and should have already
        # populated the Language table before this test ever runs.
        tags = set(Language.objects.values_list("lang_tag", flat=True))
        for _, tag in DEFAULT_LANGUAGES:
            self.assertIn(tag, tags)

    def test_seed_languages_is_idempotent(self):
        before_count = Language.objects.count()
        seed_languages(Language)
        self.assertEqual(Language.objects.count(), before_count)

    def test_seed_languages_does_not_overwrite_renamed_language(self):
        english = Language.objects.get(lang_tag="en")
        english.language = "Custom English Name"
        english.save()

        seed_languages(Language)

        english.refresh_from_db()
        self.assertEqual(english.language, "Custom English Name")

    def test_seed_languages_adds_missing_language(self):
        Language.objects.filter(lang_tag="eo").delete()

        seed_languages(Language)

        self.assertTrue(
            Language.objects.filter(lang_tag="eo", language="Esperanto").exists()
        )
