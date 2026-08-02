"""Deterministic country and U.S.-state filtering for public job postings."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from .models import DiscoveredJob, WorkplaceType

# ISO-style values keep URLs compact while labels remain user friendly. The
# list intentionally covers sovereign states most likely to appear in public
# job feeds; unrecognized/unspecified locations remain visible when no filter
# is selected.
COUNTRY_OPTIONS: tuple[tuple[str, str], ...] = (
    ("AF", "Afghanistan"), ("AL", "Albania"), ("DZ", "Algeria"),
    ("AD", "Andorra"), ("AO", "Angola"), ("AG", "Antigua and Barbuda"),
    ("AR", "Argentina"), ("AM", "Armenia"), ("AU", "Australia"),
    ("AT", "Austria"), ("AZ", "Azerbaijan"), ("BS", "Bahamas"),
    ("BH", "Bahrain"), ("BD", "Bangladesh"), ("BB", "Barbados"),
    ("BY", "Belarus"), ("BE", "Belgium"), ("BZ", "Belize"),
    ("BJ", "Benin"), ("BT", "Bhutan"), ("BO", "Bolivia"),
    ("BA", "Bosnia and Herzegovina"), ("BW", "Botswana"), ("BR", "Brazil"),
    ("BN", "Brunei"), ("BG", "Bulgaria"), ("BF", "Burkina Faso"),
    ("BI", "Burundi"), ("CV", "Cabo Verde"), ("KH", "Cambodia"),
    ("CM", "Cameroon"), ("CA", "Canada"), ("CF", "Central African Republic"),
    ("TD", "Chad"), ("CL", "Chile"), ("CN", "China"),
    ("CO", "Colombia"), ("KM", "Comoros"), ("CG", "Congo"),
    ("CD", "Congo, Democratic Republic of the"), ("CR", "Costa Rica"),
    ("CI", "Côte d’Ivoire"), ("HR", "Croatia"), ("CU", "Cuba"),
    ("CY", "Cyprus"), ("CZ", "Czechia"), ("DK", "Denmark"),
    ("DJ", "Djibouti"), ("DM", "Dominica"), ("DO", "Dominican Republic"),
    ("EC", "Ecuador"), ("EG", "Egypt"), ("SV", "El Salvador"),
    ("GQ", "Equatorial Guinea"), ("ER", "Eritrea"), ("EE", "Estonia"),
    ("SZ", "Eswatini"), ("ET", "Ethiopia"), ("FJ", "Fiji"),
    ("FI", "Finland"), ("FR", "France"), ("GA", "Gabon"),
    ("GM", "Gambia"), ("GE", "Georgia"), ("DE", "Germany"),
    ("GH", "Ghana"), ("GR", "Greece"), ("GD", "Grenada"),
    ("GT", "Guatemala"), ("GN", "Guinea"), ("GW", "Guinea-Bissau"),
    ("GY", "Guyana"), ("HT", "Haiti"), ("HN", "Honduras"),
    ("HU", "Hungary"), ("IS", "Iceland"), ("IN", "India"),
    ("ID", "Indonesia"), ("IR", "Iran"), ("IQ", "Iraq"),
    ("IE", "Ireland"), ("IL", "Israel"), ("IT", "Italy"),
    ("JM", "Jamaica"), ("JP", "Japan"), ("JO", "Jordan"),
    ("KZ", "Kazakhstan"), ("KE", "Kenya"), ("KI", "Kiribati"),
    ("KP", "Korea, North"), ("KR", "Korea, South"), ("KW", "Kuwait"),
    ("KG", "Kyrgyzstan"), ("LA", "Laos"), ("LV", "Latvia"),
    ("LB", "Lebanon"), ("LS", "Lesotho"), ("LR", "Liberia"),
    ("LY", "Libya"), ("LI", "Liechtenstein"), ("LT", "Lithuania"),
    ("LU", "Luxembourg"), ("MG", "Madagascar"), ("MW", "Malawi"),
    ("MY", "Malaysia"), ("MV", "Maldives"), ("ML", "Mali"),
    ("MT", "Malta"), ("MH", "Marshall Islands"), ("MR", "Mauritania"),
    ("MU", "Mauritius"), ("MX", "Mexico"), ("FM", "Micronesia"),
    ("MD", "Moldova"), ("MC", "Monaco"), ("MN", "Mongolia"),
    ("ME", "Montenegro"), ("MA", "Morocco"), ("MZ", "Mozambique"),
    ("MM", "Myanmar"), ("NA", "Namibia"), ("NR", "Nauru"),
    ("NP", "Nepal"), ("NL", "Netherlands"), ("NZ", "New Zealand"),
    ("NI", "Nicaragua"), ("NE", "Niger"), ("NG", "Nigeria"),
    ("MK", "North Macedonia"), ("NO", "Norway"), ("OM", "Oman"),
    ("PK", "Pakistan"), ("PW", "Palau"), ("PS", "Palestine"),
    ("PA", "Panama"), ("PG", "Papua New Guinea"), ("PY", "Paraguay"),
    ("PE", "Peru"), ("PH", "Philippines"), ("PL", "Poland"),
    ("PT", "Portugal"), ("QA", "Qatar"), ("RO", "Romania"),
    ("RU", "Russia"), ("RW", "Rwanda"), ("KN", "Saint Kitts and Nevis"),
    ("LC", "Saint Lucia"), ("VC", "Saint Vincent and the Grenadines"),
    ("WS", "Samoa"), ("SM", "San Marino"), ("ST", "São Tomé and Príncipe"),
    ("SA", "Saudi Arabia"), ("SN", "Senegal"), ("RS", "Serbia"),
    ("SC", "Seychelles"), ("SL", "Sierra Leone"), ("SG", "Singapore"),
    ("SK", "Slovakia"), ("SI", "Slovenia"), ("SB", "Solomon Islands"),
    ("SO", "Somalia"), ("ZA", "South Africa"), ("SS", "South Sudan"),
    ("ES", "Spain"), ("LK", "Sri Lanka"), ("SD", "Sudan"),
    ("SR", "Suriname"), ("SE", "Sweden"), ("CH", "Switzerland"),
    ("SY", "Syria"), ("TW", "Taiwan"), ("TJ", "Tajikistan"),
    ("TZ", "Tanzania"), ("TH", "Thailand"), ("TL", "Timor-Leste"),
    ("TG", "Togo"), ("TO", "Tonga"), ("TT", "Trinidad and Tobago"),
    ("TN", "Tunisia"), ("TR", "Türkiye"), ("TM", "Turkmenistan"),
    ("TV", "Tuvalu"), ("UG", "Uganda"), ("UA", "Ukraine"),
    ("AE", "United Arab Emirates"), ("GB", "United Kingdom"),
    ("US", "United States"), ("UY", "Uruguay"), ("UZ", "Uzbekistan"),
    ("VU", "Vanuatu"), ("VA", "Vatican City"), ("VE", "Venezuela"),
    ("VN", "Vietnam"), ("YE", "Yemen"), ("ZM", "Zambia"),
    ("ZW", "Zimbabwe"),
)

US_STATE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"),
    ("AR", "Arkansas"), ("CA", "California"), ("CO", "Colorado"),
    ("CT", "Connecticut"), ("DE", "Delaware"), ("DC", "District of Columbia"),
    ("FL", "Florida"), ("GA", "Georgia"), ("HI", "Hawaii"),
    ("ID", "Idaho"), ("IL", "Illinois"), ("IN", "Indiana"),
    ("IA", "Iowa"), ("KS", "Kansas"), ("KY", "Kentucky"),
    ("LA", "Louisiana"), ("ME", "Maine"), ("MD", "Maryland"),
    ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"),
    ("MS", "Mississippi"), ("MO", "Missouri"), ("MT", "Montana"),
    ("NE", "Nebraska"), ("NV", "Nevada"), ("NH", "New Hampshire"),
    ("NJ", "New Jersey"), ("NM", "New Mexico"), ("NY", "New York"),
    ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"),
    ("OK", "Oklahoma"), ("OR", "Oregon"), ("PA", "Pennsylvania"),
    ("RI", "Rhode Island"), ("SC", "South Carolina"), ("SD", "South Dakota"),
    ("TN", "Tennessee"), ("TX", "Texas"), ("UT", "Utah"),
    ("VT", "Vermont"), ("VA", "Virginia"), ("WA", "Washington"),
    ("WV", "West Virginia"), ("WI", "Wisconsin"), ("WY", "Wyoming"),
)

_COUNTRY_LABELS = dict(COUNTRY_OPTIONS)
_STATE_LABELS = dict(US_STATE_OPTIONS)


def _key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


_COUNTRY_NAME_KEYS = {code: _key(label) for code, label in COUNTRY_OPTIONS}
_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    code: (name,) for code, name in _COUNTRY_NAME_KEYS.items()
}
_COUNTRY_ALIASES.update(
    {
        "US": ("united states", "united states of america", "usa", "u s a", "america"),
        "GB": ("united kingdom", "great britain", "britain", "england", "scotland", "wales", "northern ireland"),
        "AE": ("united arab emirates", "uae", "u a e"),
        "KR": ("south korea", "republic of korea", "korea south"),
        "KP": ("north korea", "democratic peoples republic of korea", "korea north"),
        "CZ": ("czechia", "czech republic"),
        "CI": ("cote d ivoire", "ivory coast"),
        "TR": ("turkiye", "turkey"),
        "VN": ("vietnam", "viet nam"),
        "RU": ("russia", "russian federation"),
        "TW": ("taiwan", "taiwan roc"),
    }
)

# Common subnational labels make country filtering useful even when feeds omit
# the country name, as many North American sources do.
_CANADIAN_REGION_KEYS = tuple(
    _key(value)
    for value in (
        "Alberta", "British Columbia", "Manitoba", "New Brunswick",
        "Newfoundland and Labrador", "Northwest Territories", "Nova Scotia",
        "Nunavut", "Ontario", "Prince Edward Island", "Quebec",
        "Saskatchewan", "Yukon",
    )
)
_CANADIAN_REGION_CODES = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}


def normalize_country_filter(value: object) -> str:
    code = str(value or "").strip().upper()
    return code if code in _COUNTRY_LABELS else ""


def normalize_us_state_filter(value: object) -> str:
    code = str(value or "").strip().upper()
    return code if code in _STATE_LABELS else ""


def job_location_texts(job: DiscoveredJob) -> tuple[str, ...]:
    values: list[str] = []
    for value in (job.location, *(job.locations or ())):
        text = " ".join(str(value or "").split()).strip()
        if text and text.casefold() not in {item.casefold() for item in values}:
            values.append(text)
    return tuple(values)


_LOCATION_CODE_DELIMITERS = r",;/()\-\[\]{}\"'“”‘’"


def _contains_location_code(text: str, code: str) -> bool:
    """Return true when a two-letter region code is location-delimited.

    Job feeds sometimes serialize multiple locations into a display string, for
    example ``["Milwaukee, WI", "Chicago, IL"]``. Quotes and brackets must be
    treated as location separators, while ordinary prose should not turn short
    words such as ``IN`` or ``OR`` into state matches.
    """

    escaped = re.escape(code.upper())
    delimiters = _LOCATION_CODE_DELIMITERS
    return bool(
        re.search(
            rf"(?:^|[{delimiters}]\s*|\s){escaped}"
            rf"(?=$|\s*[{delimiters}]|\s+(?:AND|OR)\b|\s*$)",
            str(text or "").upper(),
        )
    )


def infer_us_state_codes(job: DiscoveredJob) -> frozenset[str]:
    result: set[str] = set()
    for text in job_location_texts(job):
        normalized = _key(text)
        for code, label in US_STATE_OPTIONS:
            label_key = _key(label)
            if re.search(rf"(?:^|\s){re.escape(label_key)}(?:$|\s)", f" {normalized} "):
                result.add(code)
                continue
            if _contains_location_code(text, code):
                result.add(code)
    return frozenset(result)


def infer_country_codes(job: DiscoveredJob) -> frozenset[str]:
    texts = job_location_texts(job)
    normalized_text = " | ".join(_key(text) for text in texts)
    result: set[str] = set()

    if infer_us_state_codes(job):
        result.add("US")

    for code, aliases in _COUNTRY_ALIASES.items():
        for alias in aliases:
            if re.search(rf"(?:^|\s){re.escape(alias)}(?:$|\s)", f" {normalized_text} "):
                result.add(code)
                break

    for text in texts:
        raw_upper = text.upper()
        # Explicit U.S. abbreviations are safe because no U.S. state uses US.
        if _contains_location_code(text, "US") or _contains_location_code(text, "USA"):
            result.add("US")
        # UK is commonly emitted by job feeds even though GB is the ISO code.
        if _contains_location_code(text, "UK"):
            result.add("GB")

        normalized = _key(text)
        if any(re.search(rf"(?:^|\s){re.escape(region)}(?:$|\s)", f" {normalized} ") for region in _CANADIAN_REGION_KEYS):
            result.add("CA")
        if any(_contains_location_code(text, code) for code in _CANADIAN_REGION_CODES):
            result.add("CA")

    return frozenset(result)


def job_matches_location_filters(
    job: DiscoveredJob,
    *,
    country_code: object = "",
    us_state_code: object = "",
) -> bool:
    selected_country = normalize_country_filter(country_code)
    selected_state = normalize_us_state_filter(us_state_code)
    if not selected_country and not selected_state:
        return True

    countries = infer_country_codes(job)
    states = infer_us_state_codes(job)

    if selected_country and selected_country not in countries:
        return False

    if selected_state:
        if selected_state in states:
            return True
        # Nationwide U.S.-remote roles are eligible in every state when the
        # posting identifies the country but no specific state restriction.
        return (
            not states
            and "US" in countries
            and job.workplace_type is WorkplaceType.REMOTE
        )

    return True
