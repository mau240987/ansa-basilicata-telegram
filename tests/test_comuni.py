import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import comuni as c  # noqa: E402


class ComuniTests(unittest.TestCase):
    def test_list_is_complete_and_unique(self):
        self.assertEqual(len(c.COMUNI), 131)
        self.assertEqual(len(set(c.COMUNI)), 131)
        for alias, canon in c.ALIASES.items():
            self.assertIn(canon, c.COMUNI, alias)
        self.assertTrue((c.AMBIGUOUS | c.SURNAMES) <= set(c.COMUNI))
        self.assertFalse(c.AMBIGUOUS & c.SURNAMES)

    def test_hashtag_format(self):
        self.assertEqual(c.hashtag("Matera"), "#Matera")
        self.assertEqual(c.hashtag("Rionero in Vulture"), "#RioneroInVulture")
        self.assertEqual(c.hashtag("Sant'Arcangelo"), "#SantArcangelo")
        self.assertEqual(c.hashtag("San Martino d'Agri"), "#SanMartinoDAgri")

    def test_every_comune_produces_a_valid_hashtag(self):
        for name in c.COMUNI:
            self.assertRegex(c.hashtag(name), r"^#[A-Za-z]+$", name)

    def test_order_of_appearance_and_limit(self):
        self.assertEqual(
            c.find_places("Accordo tra Policoro e Matera", "Firmato anche da Potenza e Melfi"),
            ["Policoro", "Matera", "Potenza"],
        )

    def test_case_sensitive_and_whole_words(self):
        self.assertEqual(c.find_places("La potenza del vento nel Materano"), [])
        self.assertEqual(c.find_places("A Matera e a Potenza"), ["Matera", "Potenza"])

    def test_aliases_and_extra_places(self):
        self.assertEqual(c.find_places("Incidente a Rionero, traffico verso Metaponto"),
                         ["Rionero in Vulture", "Metaponto"])
        self.assertEqual(c.find_places("Rionero in Vulture, festa patronale"), ["Rionero in Vulture"])

    def test_longer_name_wins_over_contained_one(self):
        self.assertEqual(c.find_places("Frana a Vietri di Potenza"), ["Vietri di Potenza"])
        self.assertEqual(c.find_places("Vietri di Potenza e Potenza"), ["Vietri di Potenza", "Potenza"])

    def test_curly_apostrophe(self):
        self.assertEqual(c.find_places("Sagra a Sant’Arcangelo"), ["Sant'Arcangelo"])

    def test_ambiguous_names_need_context(self):
        self.assertEqual(c.find_places("Bella giornata di sole, anzi caldo"), [])
        self.assertEqual(c.find_places("Tito Boeri parla di pensioni"), [])
        self.assertEqual(c.find_places("Prezzi del carbone e rotonda chiusa"), [])
        self.assertEqual(c.find_places("Nuova scuola a Bella"), ["Bella"])
        self.assertEqual(c.find_places("Il sindaco di Anzi incontra la Regione"), ["Anzi"])
        self.assertEqual(c.find_places("Incendio nella zona industriale di Tito Scalo"), ["Tito"])
        self.assertEqual(c.find_places("A Rotonda arriva il Pollino Festival"), ["Rotonda"])

    def test_real_headlines(self):
        # Casi presi dal feed ANSA Basilicata del 4 novembre 2026.
        self.assertEqual(c.find_places("Latronico, con la Manovra si rafforza il Servizio sanitario"), [])
        self.assertEqual(c.find_places("Fondo sanitario", "Latronico risponde a Bertolaso"), [])
        self.assertEqual(c.find_places("'Sul potabilizzatore sia coinvolto il Comune di Missanello'"),
                         ["Missanello"])
        self.assertEqual(c.find_places("'Potenza merita di più'"), ["Potenza"])
        self.assertEqual(c.find_places("Strada provinciale 3 Maratea-Trecchina"), ["Maratea", "Trecchina"])
        self.assertEqual(c.find_places("Il governatore a Tito per l'evento"), ["Tito"])
        self.assertEqual(c.find_places("Interdittive nel Melfese e nel Potentino"), [])

    def test_review_findings(self):
        # Il cognome dell'assessore non diventa hashtag nemmeno dopo "a" o "da".
        for text in ("Interrogazione a Latronico sulle liste d'attesa",
                     "Chiesto a Latronico di intervenire", "Misure chieste da Latronico"):
            self.assertEqual(c.find_places(text), [], text)
        self.assertEqual(c.find_places("Il sindaco di Latronico chiede fondi"), ["Latronico"])
        self.assertEqual(c.find_places("Riaprono le Terme: Latronico Terme in festa"), ["Latronico"])
        self.assertEqual(c.find_places("Intervista a Tito Boeri"), [])
        self.assertEqual(c.find_places("Potenza Picena: incendio in un capannone"), [])
        self.assertEqual(c.find_places("Potenza, Picena e Matera"), ["Potenza", "Matera"])


if __name__ == "__main__":
    unittest.main()
