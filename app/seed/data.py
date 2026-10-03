"""Deterministic synthetic catalog for the three demo shops.

Prices are synthetic demo values (NOT real offers or exchange rates) written as decimal strings and
converted to integer minor units with ``Decimal``. Manufacturers are fictional. ``country_of_origin`` is
a per-product attribute that is independent from the shop country and from the manufacturer name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.countries import ISO_3166_1_ALPHA2
from app.shops import CURRENCY_EXPONENT, SHOPS

SEED_NAMESPACE = uuid.UUID("8f2b8c52-6a52-4a4f-9d54-6a8ab0b8f7a1")

SHOP_ORDER = ("shop-pl", "shop-de", "shop-ru")
LANG = {"shop-pl": "pl", "shop-de": "de", "shop-ru": "en"}


def seed_uuid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, ":".join(parts))


def to_minor(amount: str, currency: str) -> int:
    """Decimal string -> integer minor units with controlled (half-up) rounding."""
    exp = CURRENCY_EXPONENT[currency]
    value = (Decimal(amount) * (10**exp)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(value)


CATEGORIES: dict[str, dict[str, str]] = {
    "paper": {"pl": "Papier", "de": "Papier", "en": "Paper"},
    "writing": {"pl": "Artykuły piśmiennicze", "de": "Schreibwaren", "en": "Writing instruments"},
    "archiving": {"pl": "Archiwizacja", "de": "Archivierung", "en": "Archiving"},
    "notebooks": {"pl": "Notesy", "de": "Notizbücher", "en": "Notebooks"},
    "envelopes": {"pl": "Koperty", "de": "Briefumschläge", "en": "Envelopes"},
    "accessories": {"pl": "Akcesoria biurowe", "de": "Bürozubehör", "en": "Office accessories"},
}
CATEGORY_ORDER = list(CATEGORIES)

UNITS = {
    "sheet": {"pl": "arkusz", "de": "Blatt", "en": "sheet"},
    "piece": {"pl": "sztuka", "de": "Stück", "en": "piece"},
    "leaf": {"pl": "kartka", "de": "Blatt", "en": "sheet"},
}


@dataclass(frozen=True)
class Item:
    short: str
    canonical: str
    category: str
    unit: str
    units_per_pack: int
    prices: tuple[str, str, str]  # PLN, EUR, RUB (gross, per pack)
    pl: tuple[str, str]
    de: tuple[str, str]
    en: tuple[str, str]
    compat: tuple[str, ...] = field(default_factory=tuple)


ITEMS: list[Item] = [
    Item("PAP-A4-500", "PAPER-A4-80-500", "paper", "sheet", 500, ("24.90", "5.90", "590.00"),
         ("Papier ksero A4 80 g/m2, ryza 500 arkuszy", "Biały papier uniwersalny do drukarek i kopiarek, gramatura 80 g/m2, ryza po 500 arkuszy."),
         ("Kopierpapier A4 80 g/m2, Ries 500 Blatt", "Weißes Universalpapier für Drucker und Kopierer, 80 g/m2, ein Ries mit 500 Blatt."),
         ("A4 copy paper 80 gsm, ream of 500 sheets", "White all-purpose paper for printers and copiers, 80 gsm, one ream of 500 sheets.")),
    Item("PAP-A4-2500", "PAPER-A4-80-2500", "paper", "sheet", 2500, ("109.00", "26.90", "2650.00"),
         ("Papier ksero A4 80 g/m2, karton 5 ryz (2500 arkuszy)", "Karton zbiorczy z pięcioma ryzami papieru A4 80 g/m2, razem 2500 arkuszy."),
         ("Kopierpapier A4 80 g/m2, Karton 5 Ries (2500 Blatt)", "Umkarton mit fünf Ries Papier A4 80 g/m2, insgesamt 2500 Blatt."),
         ("A4 copy paper 80 gsm, carton of 5 reams (2500 sheets)", "Carton with five reams of A4 80 gsm paper, 2500 sheets in total.")),
    Item("PAP-A3-500", "PAPER-A3-80-500", "paper", "sheet", 500, ("54.90", "13.50", "1290.00"),
         ("Papier ksero A3 80 g/m2, ryza 500 arkuszy", "Papier biurowy w formacie A3, 80 g/m2, ryza po 500 arkuszy."),
         ("Kopierpapier A3 80 g/m2, Ries 500 Blatt", "Büropapier im Format A3, 80 g/m2, ein Ries mit 500 Blatt."),
         ("A3 copy paper 80 gsm, ream of 500 sheets", "Office paper in A3 format, 80 gsm, one ream of 500 sheets.")),
    Item("PAP-A4-COLOR-250", "PAPER-A4-80-COLOR-250", "paper", "sheet", 250, ("26.90", "6.50", "640.00"),
         ("Papier kolorowy A4 80 g/m2, mix pastelowy, 250 arkuszy", "Pastelowy mix pięciu kolorów, papier A4 80 g/m2, opakowanie 250 arkuszy."),
         ("Farbiges Kopierpapier A4 80 g/m2, Pastellmix, 250 Blatt", "Pastellmix aus fünf Farben, Papier A4 80 g/m2, Packung mit 250 Blatt."),
         ("Coloured paper A4 80 gsm, pastel mix, 250 sheets", "Pastel mix of five colours, A4 paper 80 gsm, pack of 250 sheets.")),
    Item("PAP-A4-100G-250", "PAPER-A4-100-250", "paper", "sheet", 250, ("29.90", "7.20", "690.00"),
         ("Papier satynowany A4 100 g/m2, 250 arkuszy", "Gładki papier o podwyższonej gramaturze 100 g/m2 do dokumentów reprezentacyjnych, 250 arkuszy."),
         ("Satiniertes Papier A4 100 g/m2, 250 Blatt", "Glattes Papier mit erhöhter Grammatur von 100 g/m2 für repräsentative Dokumente, 250 Blatt."),
         ("Satin paper A4 100 gsm, 250 sheets", "Smooth heavier paper, 100 gsm, for presentation documents, 250 sheets.")),
    Item("PEN-BALL-BLUE-10", "PEN-BALLPOINT-BLUE-10", "writing", "piece", 10, ("14.90", "3.90", "350.00"),
         ("Długopisy niebieskie, 10 sztuk", "Długopisy z niebieskim wkładem, końcówka 0,7 mm, opakowanie 10 sztuk."),
         ("Kugelschreiber blau, 10 Stück", "Kugelschreiber mit blauer Mine, Strichstärke 0,7 mm, Packung mit 10 Stück."),
         ("Ballpoint pens, blue, 10 pieces", "Ballpoint pens with blue ink, 0.7 mm tip, pack of 10.")),
    Item("PEN-BALL-BLACK-10", "PEN-BALLPOINT-BLACK-10", "writing", "piece", 10, ("14.90", "3.90", "350.00"),
         ("Długopisy czarne, 10 sztuk", "Długopisy z czarnym wkładem, końcówka 0,7 mm, opakowanie 10 sztuk."),
         ("Kugelschreiber schwarz, 10 Stück", "Kugelschreiber mit schwarzer Mine, Strichstärke 0,7 mm, Packung mit 10 Stück."),
         ("Ballpoint pens, black, 10 pieces", "Ballpoint pens with black ink, 0.7 mm tip, pack of 10.")),
    Item("PEN-GEL-BLACK-5", "PEN-GEL-BLACK-5", "writing", "piece", 5, ("17.90", "4.50", "420.00"),
         ("Pióra żelowe czarne, 5 sztuk", "Pióra żelowe z szybkoschnącym czarnym tuszem, końcówka 0,5 mm, 5 sztuk."),
         ("Gelschreiber schwarz, 5 Stück", "Gelschreiber mit schnell trocknender schwarzer Tinte, Strichstärke 0,5 mm, 5 Stück."),
         ("Gel pens, black, 5 pieces", "Gel pens with quick-drying black ink, 0.5 mm tip, 5 pieces.")),
    Item("MARK-HILITE-4", "MARKER-HIGHLIGHTER-MIX-4", "writing", "piece", 4, ("12.90", "3.40", "310.00"),
         ("Zakreślacze fluorescencyjne, 4 kolory", "Zestaw czterech zakreślaczy z końcówką ściętą 1–5 mm w kolorach żółtym, zielonym, różowym i pomarańczowym."),
         ("Textmarker, 4 Farben", "Set aus vier Textmarkern mit Keilspitze 1–5 mm in Gelb, Grün, Rosa und Orange."),
         ("Highlighters, 4 colours", "Set of four highlighters with a 1–5 mm chisel tip in yellow, green, pink and orange.")),
    Item("PENCIL-HB-12", "PENCIL-HB-12", "writing", "piece", 12, ("11.90", "2.90", "270.00"),
         ("Ołówki drewniane HB, 12 sztuk", "Ołówki z drewna lipowego o twardości HB, opakowanie 12 sztuk."),
         ("Bleistifte HB, 12 Stück", "Bleistifte aus Lindenholz mit Härtegrad HB, Packung mit 12 Stück."),
         ("Wooden pencils HB, 12 pieces", "Basswood pencils with HB hardness, pack of 12.")),
    Item("MARK-WB-4", "MARKER-WHITEBOARD-4", "writing", "piece", 4, ("19.90", "5.20", "470.00"),
         ("Markery do tablic suchościeralnych, 4 kolory", "Cztery markery do tablic suchościeralnych z okrągłą końcówką, łatwo ścieralne."),
         ("Whiteboard-Marker, 4 Farben", "Vier Whiteboard-Marker mit Rundspitze, leicht abwischbar."),
         ("Whiteboard markers, 4 colours", "Four dry-erase markers with a round tip, easy to wipe off.")),
    Item("BIND-A4-75-1", "BINDER-A4-75", "archiving", "piece", 1, ("12.90", "3.20", "290.00"),
         ("Segregator A4 75 mm", "Segregator z mechanizmem dźwigniowym, format A4, grzbiet 75 mm, okładka z kartonu laminowanego."),
         ("Ordner A4 75 mm", "Hebelordner im Format A4 mit 75 mm Rückenbreite, Einband aus laminierter Pappe."),
         ("A4 lever arch file 75 mm", "Lever arch file, A4, 75 mm spine, laminated cardboard cover.")),
    Item("BIND-A4-50-1", "BINDER-A4-50", "archiving", "piece", 1, ("10.90", "2.80", "250.00"),
         ("Segregator A4 50 mm", "Segregator z mechanizmem dźwigniowym, format A4, grzbiet 50 mm."),
         ("Ordner A4 50 mm", "Hebelordner im Format A4 mit 50 mm Rückenbreite."),
         ("A4 lever arch file 50 mm", "Lever arch file, A4, 50 mm spine.")),
    Item("SLEEVE-A4-100", "SLEEVE-PUNCHED-A4-100", "archiving", "piece", 100, ("13.90", "3.60", "330.00"),
         ("Koszulki foliowe A4 krystaliczne, 100 sztuk", "Przezroczyste koszulki na dokumenty A4 z perforacją, grubość 40 mikronów, 100 sztuk."),
         ("Prospekthüllen A4 klar, 100 Stück", "Transparente gelochte Prospekthüllen A4, Stärke 40 Mikrometer, 100 Stück."),
         ("Punched pockets A4 clear, 100 pieces", "Transparent punched document sleeves, A4, 40 micron, 100 pieces.")),
    Item("ARCHBOX-A4-80", "ARCHIVE-BOX-A4-80", "archiving", "piece", 1, ("9.90", "2.60", "240.00"),
         ("Pudło archiwizacyjne A4, szerokość 80 mm", "Składane pudło z tektury falistej na segregatory i dokumenty A4, szerokość 80 mm."),
         ("Archivbox A4, Breite 80 mm", "Faltbarer Archivkarton aus Wellpappe für Ordner und Dokumente A4, Breite 80 mm."),
         ("Archive box A4, 80 mm wide", "Foldable corrugated cardboard box for A4 binders and documents, 80 mm wide.")),
    Item("NOTE-A5-80", "NOTEBOOK-A5-80", "notebooks", "leaf", 80, ("8.90", "2.40", "220.00"),
         ("Notes A5 w kratkę, 80 kartek", "Notes w twardej oprawie, format A5, papier 70 g/m2 w kratkę, 80 kartek."),
         ("Notizbuch A5 kariert, 80 Blatt", "Notizbuch mit festem Einband, Format A5, 70 g/m2 kariert, 80 Blatt."),
         ("A5 notebook, squared, 80 sheets", "Hardcover notebook, A5, 70 gsm squared paper, 80 sheets.")),
    Item("NOTE-A4-100", "NOTEBOOK-A4-100", "notebooks", "leaf", 100, ("14.90", "3.80", "340.00"),
         ("Notes A4 w linie, 100 kartek", "Notes spiralowy, format A4, papier 70 g/m2 w linie, 100 kartek."),
         ("Notizblock A4 liniert, 100 Blatt", "Spiralblock im Format A4, 70 g/m2 liniert, 100 Blatt."),
         ("A4 notebook, ruled, 100 sheets", "Spiral-bound notebook, A4, 70 gsm ruled paper, 100 sheets.")),
    Item("STICKY-76-100", "STICKY-NOTES-76X76-100", "notebooks", "leaf", 100, ("6.90", "1.90", "170.00"),
         ("Karteczki samoprzylepne 76x76 mm, żółte, 100 kartek", "Żółte karteczki samoprzylepne 76x76 mm, blok 100 kartek."),
         ("Haftnotizen 76x76 mm, gelb, 100 Blatt", "Gelbe Haftnotizen 76x76 mm, Block mit 100 Blatt."),
         ("Sticky notes 76x76 mm, yellow, 100 sheets", "Yellow sticky notes 76x76 mm, pad of 100 sheets.")),
    Item("ENV-C5-50", "ENVELOPE-C5-50", "envelopes", "piece", 50, ("16.90", "4.20", "390.00"),
         ("Koperty C5 białe samoklejące, 50 sztuk", "Białe koperty C5 (162x229 mm) z paskiem samoklejącym, opakowanie 50 sztuk."),
         ("Briefumschläge C5 weiß, selbstklebend, 50 Stück", "Weiße Briefumschläge C5 (162x229 mm) mit Haftstreifen, Packung mit 50 Stück."),
         ("C5 envelopes, white, self-adhesive, 50 pieces", "White C5 envelopes (162x229 mm) with peel-off strip, pack of 50.")),
    Item("ENV-DL-50", "ENVELOPE-DL-50", "envelopes", "piece", 50, ("14.90", "3.60", "340.00"),
         ("Koperty DL białe samoklejące, 50 sztuk", "Białe koperty DL (110x220 mm) z paskiem samoklejącym, opakowanie 50 sztuk."),
         ("Briefumschläge DL weiß, selbstklebend, 50 Stück", "Weiße Briefumschläge DL (110x220 mm) mit Haftstreifen, Packung mit 50 Stück."),
         ("DL envelopes, white, self-adhesive, 50 pieces", "White DL envelopes (110x220 mm) with peel-off strip, pack of 50.")),
    Item("STAPLER-24-6", "STAPLER-24-6", "accessories", "piece", 1, ("24.90", "6.50", "590.00"),
         ("Zszywacz biurowy na zszywki 24/6", "Metalowy zszywacz biurowy, zszywa do 25 kartek, kompatybilny wyłącznie ze zszywkami 24/6."),
         ("Büro-Heftgerät für Heftklammern 24/6", "Heftgerät aus Metall für bis zu 25 Blatt, nur für Heftklammern 24/6 geeignet."),
         ("Office stapler for 24/6 staples", "Metal office stapler, staples up to 25 sheets, compatible only with 24/6 staples.")),
    Item("STAPLES-24-6-1000", "STAPLES-24-6-1000", "accessories", "piece", 1000, ("4.90", "1.30", "120.00"),
         ("Zszywki 24/6, 1000 sztuk", "Zszywki biurowe 24/6 ocynkowane, opakowanie 1000 sztuk. Pasują do zszywacza 24/6."),
         ("Heftklammern 24/6, 1000 Stück", "Verzinkte Heftklammern 24/6, Packung mit 1000 Stück. Passend für Heftgeräte 24/6."),
         ("Staples 24/6, 1000 pieces", "Galvanised 24/6 staples, pack of 1000. Fits 24/6 staplers."),
         compat=("STAPLER-24-6",)),
    Item("TAPE-19-33", "TAPE-ADHESIVE-19X33", "accessories", "piece", 1, ("5.90", "1.60", "140.00"),
         ("Taśma klejąca przezroczysta 19 mm x 33 m", "Przezroczysta taśma biurowa 19 mm x 33 m, rolka."),
         ("Klebeband transparent 19 mm x 33 m", "Transparentes Büroklebeband 19 mm x 33 m, eine Rolle."),
         ("Clear adhesive tape 19 mm x 33 m", "Transparent office tape 19 mm x 33 m, one roll.")),
    Item("CLIPS-28-100", "PAPERCLIPS-28-100", "accessories", "piece", 100, ("4.50", "1.20", "110.00"),
         ("Spinacze biurowe 28 mm, 100 sztuk", "Ocynkowane spinacze biurowe 28 mm, pudełko 100 sztuk."),
         ("Büroklammern 28 mm, 100 Stück", "Verzinkte Büroklammern 28 mm, Schachtel mit 100 Stück."),
         ("Paper clips 28 mm, 100 pieces", "Galvanised paper clips 28 mm, box of 100.")),
]

# Index (in ITEMS) -> stock override per shop: (PL, DE, RU). Zero = unavailable, 1-5 = low stock.
_STOCK_OVERRIDE: dict[str, dict[int, int]] = {
    "shop-pl": {10: 0, 17: 0, 23: 0, 4: 3, 12: 2, 20: 4},
    "shop-de": {7: 0, 13: 0, 19: 0, 1: 4, 16: 3, 22: 5},
    "shop-ru": {2: 0, 14: 0, 21: 0, 8: 2, 11: 5, 18: 3},
}
# One discontinued (inactive) product per shop.
_INACTIVE: dict[str, int] = {"shop-pl": 7, "shop-de": 9, "shop-ru": 4}

_ORIGINS: dict[str, list[str]] = {
    "shop-pl": ["PL", "DE", "CZ", "PL", "AT", "SK"],
    "shop-de": ["DE", "CZ", "AT", "PL", "NL", "DE"],
    "shop-ru": ["RU"],
}
_MANUFACTURERS: dict[str, list[str]] = {
    "shop-pl": ["Papirus Sp. z o.o.", "Pismak", "Archiwix", "Notatnik Plus", "Kopertex", "Biurex"],
    "shop-de": ["Papierhaus Nord", "Schreibkontor", "Ordnerwerk", "Notizwerk Süd", "Kuvertia", "Büroline"],
    "shop-ru": ["Severbumaga", "Pisar Trade", "Arkhiv-Servis", "Blokhnot", "Konvert-M", "Kantsmir"],
}
_SHIPPING: dict[str, list[tuple[str, dict[str, str], str, int, int]]] = {
    # code, {name, description}, price, days, sort
    "shop-pl": [
        ("standard", {"name": "Kurier standard", "description": "Dostawa kurierska w 1–3 dni robocze."}, "15.00", 3, 1),
        ("express", {"name": "Kurier ekspres", "description": "Dostawa kurierska następnego dnia roboczego."}, "25.00", 1, 2),
    ],
    "shop-de": [
        ("standard", {"name": "Standardversand", "description": "Lieferung in 2–4 Werktagen."}, "5.90", 4, 1),
        ("express", {"name": "Expressversand", "description": "Lieferung am nächsten Werktag."}, "9.90", 1, 2),
    ],
    "shop-ru": [
        ("standard", {"name": "Standard delivery", "description": "Courier delivery within 3-5 business days."}, "350.00", 5, 1),
        ("express", {"name": "Express delivery", "description": "Courier delivery within 1-2 business days."}, "590.00", 2, 2),
    ],
}


@dataclass(frozen=True)
class ProductSeed:
    id: uuid.UUID
    sku: str
    canonical_item_code: str
    name: str
    description: str
    category_slug: str
    manufacturer_name: str
    country_of_origin: str
    unit_label: str
    units_per_pack: int
    unit_gross_minor: int
    currency: str
    stock_quantity: int
    active: bool
    compatible_with: list[str]


def products_for(shop_id: str) -> list[ProductSeed]:
    shop = SHOPS[shop_id]
    lang = LANG[shop_id]
    shop_idx = SHOP_ORDER.index(shop_id)
    origins = _ORIGINS[shop_id]
    makers = _MANUFACTURERS[shop_id]
    result: list[ProductSeed] = []
    for i, item in enumerate(ITEMS):
        name, description = getattr(item, lang)
        origin = origins[i % len(origins)]
        assert origin in ISO_3166_1_ALPHA2
        stock = _STOCK_OVERRIDE[shop_id].get(i, 25 + (i * 37 + shop_idx * 53) % 476)
        sku = f"{shop.order_prefix}-{item.short}"
        result.append(
            ProductSeed(
                id=seed_uuid(shop_id, "product", sku),
                sku=sku,
                canonical_item_code=item.canonical,
                name=name,
                description=description,
                category_slug=item.category,
                manufacturer_name=makers[CATEGORY_ORDER.index(item.category)],
                country_of_origin=origin,
                unit_label=UNITS[item.unit][lang],
                units_per_pack=item.units_per_pack,
                unit_gross_minor=to_minor(item.prices[shop_idx], shop.currency),
                currency=shop.currency,
                stock_quantity=stock,
                active=_INACTIVE[shop_id] != i,
                compatible_with=list(item.compat),
            )
        )
    return result


def categories_for(shop_id: str) -> list[tuple[uuid.UUID, str, str, int]]:
    lang = LANG[shop_id]
    return [
        (seed_uuid(shop_id, "category", slug), slug, CATEGORIES[slug][lang], idx)
        for idx, slug in enumerate(CATEGORY_ORDER)
    ]


def shipping_for(shop_id: str) -> list[dict]:
    shop = SHOPS[shop_id]
    return [
        {
            "id": seed_uuid(shop_id, "shipping", code),
            "code": code,
            "name": text["name"],
            "description": text["description"],
            "price_gross_minor": to_minor(price, shop.currency),
            "currency": shop.currency,
            "estimated_delivery_days": days,
            "sort_order": sort,
            "active": True,
        }
        for code, text, price, days, sort in _SHIPPING[shop_id]
    ]


DEMO_CUSTOMERS = {
    "agent": "Demo Agent (fictional customer)",
    "other": "Demo Other Customer (fictional customer)",
}


def demo_customer_id(shop_id: str, key: str) -> uuid.UUID:
    return seed_uuid(shop_id, "customer", key)
