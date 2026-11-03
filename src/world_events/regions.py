"""国コード（ISO 3166-1 alpha-2。情報源の都合で EU / AFRICA / ASEAN も許す。INTL・国際機関はその他）→ 地域。B7 の地域上限と B6 の テーマ × 地域 で使う。"""

REGIONS: dict[str, tuple[str, ...]] = {
    "北米": ("US", "CA", "MX"),
    "中南米": ("BR", "AR", "CL", "CO", "PE", "VE", "CU", "UY", "EC", "BO", "PY"),
    "欧州": ("GB", "DE", "FR", "IT", "ES", "NL", "BE", "SE", "NO", "DK", "FI", "PL", "CH", "AT", "IE", "PT", "CZ",
            "GR", "HU", "RO", "UA", "RU", "BY", "RS", "BG", "HR", "SK", "LT", "LV", "EE", "EU"),
    "中東": ("IL", "PS", "SA", "AE", "QA", "IR", "IQ", "SY", "LB", "JO", "KW", "OM", "YE", "TR", "BH"),
    "アフリカ": ("NG", "ZA", "EG", "KE", "ET", "GH", "TZ", "UG", "DZ", "MA", "SN", "SD", "CD", "TN", "ZW", "RW", "AFRICA"),
    "南アジア": ("IN", "PK", "BD", "LK", "NP", "AF", "BT", "MV"),
    "東アジア": ("JP", "CN", "KR", "KP", "TW", "HK", "MN", "MO"),
    "東南アジア": ("SG", "ID", "TH", "VN", "PH", "MY", "MM", "KH", "LA", "ASEAN"),
    "オセアニア": ("AU", "NZ", "FJ", "PG"),
    "中央アジア": ("KZ", "UZ", "KG", "TJ", "TM", "AZ", "AM", "GE"),
}
_COUNTRY_TO_REGION = {c: r for r, cs in REGIONS.items() for c in cs}
OTHER_REGION = "その他"


def region_of(country: str | None) -> str:
    return _COUNTRY_TO_REGION.get((country or "").upper(), OTHER_REGION)
