"""Default languages seeded into the Language table after every migration.

Covers every language in the ISO 639-1 set (that standard's own scope is
"major languages of the world"), plus a handful of languages taught at BYU
(https://cls.byu.edu/language-classes) that have no ISO 639-1 code. Every
entry is tagged with its BCP 47 primary language subtag
(https://www.rfc-editor.org/rfc/rfc5646), since Language.bcp47 requires that
scheme: the 2-letter ISO 639-1 code where one exists, otherwise the 3-letter
ISO 639-3 code (https://iso639-3.sil.org/code_tables/639/data).

Each entry is a (name, bcp47) pair. See seed_languages() below.
"""

# Languages with an ISO 639-1 code, identified here by that 2-letter code.
MAJOR_LANGUAGES = [
    ("Afar", "aa"),
    ("Abkhazian", "ab"),
    ("Avestan", "ae"),
    ("Afrikaans", "af"),
    ("Akan", "ak"),
    ("Amharic", "am"),
    ("Aragonese", "an"),
    ("Arabic", "ar"),
    ("Assamese", "as"),
    ("Avaric", "av"),
    ("Aymara", "ay"),
    ("Azerbaijani", "az"),
    ("Bashkir", "ba"),
    ("Belarusian", "be"),
    ("Bulgarian", "bg"),
    ("Bislama", "bi"),
    ("Bambara", "bm"),
    ("Bengali", "bn"),
    ("Tibetan", "bo"),
    ("Breton", "br"),
    ("Bosnian", "bs"),
    ("Catalan", "ca"),
    ("Chechen", "ce"),
    ("Chamorro", "ch"),
    ("Corsican", "co"),
    ("Cree", "cr"),
    ("Czech", "cs"),
    ("Church Slavic", "cu"),
    ("Chuvash", "cv"),
    ("Welsh", "cy"),
    ("Danish", "da"),
    ("German", "de"),
    ("Divehi", "dv"),
    ("Dzongkha", "dz"),
    ("Ewe", "ee"),
    ("Greek", "el"),
    ("English", "en"),
    ("Esperanto", "eo"),
    ("Spanish", "es"),
    ("Estonian", "et"),
    ("Basque", "eu"),
    ("Persian", "fa"),
    ("Fulah", "ff"),
    ("Finnish", "fi"),
    ("Fijian", "fj"),
    ("Faroese", "fo"),
    ("French", "fr"),
    ("Western Frisian", "fy"),
    ("Irish", "ga"),
    ("Scottish Gaelic", "gd"),
    ("Galician", "gl"),
    ("Guarani", "gn"),
    ("Gujarati", "gu"),
    ("Manx", "gv"),
    ("Hausa", "ha"),
    ("Hebrew", "he"),
    ("Hindi", "hi"),
    ("Hiri Motu", "ho"),
    ("Croatian", "hr"),
    ("Haitian Creole", "ht"),
    ("Hungarian", "hu"),
    ("Armenian", "hy"),
    ("Herero", "hz"),
    ("Interlingua", "ia"),
    ("Indonesian", "id"),
    ("Interlingue", "ie"),
    ("Igbo", "ig"),
    ("Sichuan Yi", "ii"),
    ("Inupiaq", "ik"),
    ("Ido", "io"),
    ("Icelandic", "is"),
    ("Italian", "it"),
    ("Inuktitut", "iu"),
    ("Japanese", "ja"),
    ("Javanese", "jv"),
    ("Georgian", "ka"),
    ("Kongo", "kg"),
    ("Kikuyu", "ki"),
    ("Kuanyama", "kj"),
    ("Kazakh", "kk"),
    ("Kalaallisut", "kl"),
    ("Khmer", "km"),
    ("Kannada", "kn"),
    ("Korean", "ko"),
    ("Kanuri", "kr"),
    ("Kashmiri", "ks"),
    ("Kurdish", "ku"),
    ("Komi", "kv"),
    ("Cornish", "kw"),
    ("Kyrgyz", "ky"),
    ("Latin", "la"),
    ("Luxembourgish", "lb"),
    ("Ganda", "lg"),
    ("Limburgish", "li"),
    ("Lingala", "ln"),
    ("Lao", "lo"),
    ("Lithuanian", "lt"),
    ("Luba-Katanga", "lu"),
    ("Latvian", "lv"),
    ("Malagasy", "mg"),
    ("Marshallese", "mh"),
    ("Maori", "mi"),
    ("Macedonian", "mk"),
    ("Malayalam", "ml"),
    ("Mongolian", "mn"),
    ("Marathi", "mr"),
    ("Malay", "ms"),
    ("Maltese", "mt"),
    ("Burmese", "my"),
    ("Nauru", "na"),
    ("Norwegian Bokmål", "nb"),
    ("North Ndebele", "nd"),
    ("Nepali", "ne"),
    ("Ndonga", "ng"),
    ("Dutch", "nl"),
    ("Norwegian Nynorsk", "nn"),
    ("Norwegian", "no"),
    ("South Ndebele", "nr"),
    ("Navajo", "nv"),
    ("Chichewa", "ny"),
    ("Occitan", "oc"),
    ("Ojibwa", "oj"),
    ("Oromo", "om"),
    ("Odia", "or"),
    ("Ossetian", "os"),
    ("Punjabi", "pa"),
    ("Pali", "pi"),
    ("Polish", "pl"),
    ("Pashto", "ps"),
    ("Portuguese", "pt"),
    ("Quechua", "qu"),
    ("Romansh", "rm"),
    ("Rundi", "rn"),
    ("Romanian", "ro"),
    ("Russian", "ru"),
    ("Kinyarwanda", "rw"),
    ("Sanskrit", "sa"),
    ("Sardinian", "sc"),
    ("Sindhi", "sd"),
    ("Northern Sami", "se"),
    ("Sango", "sg"),
    ("Sinhala", "si"),
    ("Slovak", "sk"),
    ("Slovenian", "sl"),
    ("Samoan", "sm"),
    ("Shona", "sn"),
    ("Somali", "so"),
    ("Albanian", "sq"),
    ("Serbian", "sr"),
    ("Swati", "ss"),
    ("Southern Sotho", "st"),
    ("Sundanese", "su"),
    ("Swedish", "sv"),
    ("Swahili", "sw"),
    ("Tamil", "ta"),
    ("Telugu", "te"),
    ("Tajik", "tg"),
    ("Thai", "th"),
    ("Tigrinya", "ti"),
    ("Turkmen", "tk"),
    ("Tagalog", "tl"),
    ("Tswana", "tn"),
    ("Tongan", "to"),
    ("Turkish", "tr"),
    ("Tsonga", "ts"),
    ("Tatar", "tt"),
    ("Twi", "tw"),
    ("Tahitian", "ty"),
    ("Uyghur", "ug"),
    ("Ukrainian", "uk"),
    ("Urdu", "ur"),
    ("Uzbek", "uz"),
    ("Venda", "ve"),
    ("Vietnamese", "vi"),
    ("Volapük", "vo"),
    ("Walloon", "wa"),
    ("Wolof", "wo"),
    ("Xhosa", "xh"),
    ("Yiddish", "yi"),
    ("Yoruba", "yo"),
    ("Zhuang", "za"),
    ("Chinese", "zh"),
    ("Zulu", "zu"),
]

# Languages taught at BYU (https://cls.byu.edu/language-classes) that have no
# ISO 639-1 code, identified here by their ISO 639-3 code (also a valid BCP
# 47 primary language subtag when no 2-letter form exists).
BYU_EXTRA_LANGUAGES = [
    ("American Sign Language", "ase"),
    ("Cebuano", "ceb"),
    ("Hawaiian", "haw"),
    ("Hiligaynon", "hil"),
    ("Hmong", "hmn"),
    ("Kekchi", "kek"),
    ("K'iche'", "quc"),
    ("Gilbertese", "gil"),
    ("S'gaw Karen", "ksw"),
]

# Languages that appear in the legacy database dump
# (var/legacy_migration/legacy_dump.sqlite3) but aren't covered above, so
# legacy subtitles can still resolve to a Language row after migration.
LEGACY_DATA_LANGUAGES = [
    ("Cakchiquel", "cak"),
]

DEFAULT_LANGUAGES = MAJOR_LANGUAGES + BYU_EXTRA_LANGUAGES + LEGACY_DATA_LANGUAGES


def seed_languages(Language):
    """Ensure every language in DEFAULT_LANGUAGES exists in the given Language model.

    Existing rows are left untouched, matched by bcp47 so that a locally
    renamed language isn't overwritten. Also skips any entry whose *name*
    already exists under a different code - both `language` and `bcp47`
    are unique, so inserting a second row for an already-present name would
    otherwise crash this post_migrate hook (and therefore `manage.py
    migrate` itself) with an IntegrityError.
    """
    existing_codes = set(Language.objects.values_list("bcp47", flat=True))
    existing_names = set(Language.objects.values_list("language", flat=True))
    Language.objects.bulk_create(
        Language(language=name, bcp47=code)
        for name, code in DEFAULT_LANGUAGES
        if code not in existing_codes and name not in existing_names
    )
