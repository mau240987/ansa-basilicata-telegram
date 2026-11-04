"""Riconosce i comuni della Basilicata in un testo e genera gli hashtag.

Elenco dei 131 comuni da https://github.com/matteocontrini/comuni-json (dati ISTAT).
"""

from __future__ import annotations

import re

COMUNI = (
    "Abriola", "Accettura", "Acerenza", "Albano di Lucania", "Aliano", "Anzi", "Armento",
    "Atella", "Avigliano", "Balvano", "Banzi", "Baragiano", "Barile", "Bella", "Bernalda",
    "Brienza", "Brindisi Montagna", "Calciano", "Calvello", "Calvera", "Campomaggiore",
    "Cancellara", "Carbone", "Castelgrande", "Castelluccio Inferiore", "Castelluccio Superiore",
    "Castelmezzano", "Castelsaraceno", "Castronuovo di Sant'Andrea", "Cersosimo", "Chiaromonte",
    "Cirigliano", "Colobraro", "Corleto Perticara", "Craco", "Episcopia", "Fardella",
    "Ferrandina", "Filiano", "Forenza", "Francavilla in Sinni", "Gallicchio", "Garaguso",
    "Genzano di Lucania", "Ginestra", "Gorgoglione", "Grassano", "Grottole", "Grumento Nova",
    "Guardia Perticara", "Irsina", "Lagonegro", "Latronico", "Laurenzana", "Lauria", "Lavello",
    "Maratea", "Marsico Nuovo", "Marsicovetere", "Maschito", "Matera", "Melfi", "Miglionico",
    "Missanello", "Moliterno", "Montalbano Jonico", "Montemilone", "Montemurro",
    "Montescaglioso", "Muro Lucano", "Nemoli", "Noepoli", "Nova Siri", "Oliveto Lucano",
    "Oppido Lucano", "Palazzo San Gervasio", "Paterno", "Pescopagano", "Picerno", "Pietragalla",
    "Pietrapertosa", "Pignola", "Pisticci", "Policoro", "Pomarico", "Potenza", "Rapolla",
    "Rapone", "Rionero in Vulture", "Ripacandida", "Rivello", "Roccanova", "Rotonda",
    "Rotondella", "Ruoti", "Ruvo del Monte", "Salandra", "San Chirico Nuovo",
    "San Chirico Raparo", "San Costantino Albanese", "San Fele", "San Giorgio Lucano",
    "San Martino d'Agri", "San Mauro Forte", "San Paolo Albanese", "San Severino Lucano",
    "Sant'Angelo Le Fratte", "Sant'Arcangelo", "Sarconi", "Sasso di Castalda",
    "Satriano di Lucania", "Savoia di Lucania", "Scanzano Jonico", "Senise", "Spinoso",
    "Stigliano", "Teana", "Terranova di Pollino", "Tito", "Tolve", "Tramutola", "Trecchina",
    "Tricarico", "Trivigno", "Tursi", "Vaglio Basilicata", "Valsinni", "Venosa",
    "Vietri di Potenza", "Viggianello", "Viggiano",
)

# Forme brevi usate spesso nei titoli -> nome ufficiale.
ALIASES = {
    "Rionero": "Rionero in Vulture",
    "Scanzano": "Scanzano Jonico",
    "Grumento": "Grumento Nova",
    "Corleto": "Corleto Perticara",
}

# Località molto citate che non sono comuni (Metaponto è frazione di Bernalda).
EXTRA_PLACES = ("Metaponto",)

# Nomi che sono anche parole comuni o nomi di persona: li riconosciamo solo con un
# contesto geografico ("a Bella", "comune di Anzi", "Tito Scalo"), e non se seguiti da
# un altro nome proprio ("a Tito Boeri").
AMBIGUOUS = frozenset({
    "Anzi", "Barile", "Bella", "Carbone", "Ginestra", "Paterno", "Rotonda", "Spinoso", "Tito",
})

# Cognomi molto frequenti nelle notizie (Latronico è l'assessore regionale alla Sanità):
# anche "chiesto a Latronico" è ambiguo, quindi serve un contesto esplicito
# ("comune di Latronico", "sindaco di Latronico", "Latronico Terme").
SURNAMES = frozenset({"Latronico"})

# Omonimi fuori regione da escludere: "Potenza Picena" è nelle Marche.
NOT_FOLLOWED_BY = {"Potenza": "Picena"}

MAX_TAGS = 3

_APOSTROPHES = str.maketrans({"\u2019": "'", "\u02bc": "'", "\u2018": "'"})
_LOCATIVE = r"(?i:\b(?:a|ad|da|in|comune di|sindac[oa] di|paese di))\s+"
_STRICT_LOCATIVE = r"(?i:\b(?:comune di|sindac[oa] di|paese di|abitanti di))\s+"


def hashtag(name: str) -> str:
    """'Rionero in Vulture' -> '#RioneroInVulture', "Sant'Arcangelo" -> '#SantArcangelo'."""
    parts = [p for p in re.split(r"[\s'\-]+", name) if p]
    return "#" + "".join(p[:1].upper() + p[1:] for p in parts)


def _build_patterns() -> list[tuple[re.Pattern[str], str]]:
    variants: list[tuple[str, str]] = [(c, c) for c in COMUNI]
    variants += [(alias, canon) for alias, canon in ALIASES.items()]
    variants += [(p, p) for p in EXTRA_PLACES]
    patterns = []
    for text, canon in variants:
        name = re.escape(text)
        if text in SURNAMES:
            regex = rf"{_STRICT_LOCATIVE}(?P<n>{name})(?!\w)|(?<!\w)(?P<s>{name})\s+(?:Scalo|Terme)\b"
        elif text in AMBIGUOUS:
            regex = (rf"{_LOCATIVE}(?P<n>{name})(?!\w)(?!\s+[A-Z][a-z])"
                     rf"|(?<!\w)(?P<s>{name})\s+Scalo\b")
        else:
            # Maiuscole obbligatorie: "Potenza" sì, "potenza" (il sostantivo) no.
            # Gli apostrofi attorno al nome sono ammessi: spesso sono virgolette ('Matera').
            not_followed = NOT_FOLLOWED_BY.get(text)
            exclude = rf"(?!\s+{not_followed}\b)" if not_followed else ""
            regex = rf"(?<!\w)(?P<n>{name})(?!\w){exclude}"
        patterns.append((re.compile(regex), canon))
    return patterns


_PATTERNS = _build_patterns()


def find_places(*texts: str, max_tags: int = MAX_TAGS) -> list[str]:
    """Comuni citati nei testi, in ordine di comparsa (prima il titolo), senza doppioni."""
    text = "\n".join(t for t in texts if t).translate(_APOSTROPHES)
    spans: list[tuple[int, int, str]] = []
    for pattern, canon in _PATTERNS:
        for match in pattern.finditer(text):
            group = "n" if match.group("n") is not None else "s"
            spans.append((match.start(group), match.end(group), canon))

    found: dict[str, int] = {}
    for start, end, canon in spans:
        # "Potenza" dentro "Vietri di Potenza" non conta come Potenza.
        inside_other = any(
            s <= start and end <= e and (e - s) > (end - start) and c != canon
            for s, e, c in spans
        )
        if inside_other:
            continue
        if canon not in found or start < found[canon]:
            found[canon] = start
    ordered = sorted(found, key=found.__getitem__)
    return ordered[:max_tags]


def hashtags_for(*texts: str, max_tags: int = MAX_TAGS) -> list[str]:
    return [hashtag(p) for p in find_places(*texts, max_tags=max_tags)]
