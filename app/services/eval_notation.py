"""Jeu de référence de la notation : des offres réelles avec la note attendue.

Rejoue la notation (sans rien écrire en base) et compare au résultat attendu.
À lancer après CHAQUE modification du prompt ou du barème, avant toute renotation complète.
"""
import argparse

from app.db import session
from app.llm.tasks import evaluer
from app.services.scoring import note_finale

# (début de l'intitulé, note minimale attendue, note maximale attendue)
REFERENCE = [
    ("Administrateur systèmes Linux%", 8, 10),
    ("Technicien Support Informatique%", 8, 10),
    ("Technicien helpdesk%", 8, 10),
    ("Technicien Support de Proximité%", 8, 10),
    ("Analyste d'Exploitation%", 7, 10),
    ("Ingénieur système Linux%", 3, 7),
    ("Ingénieur SYSOPS%", 0, 4),
    ("AWS Cloud Engineer%", 0, 3),
    ("Expert DevOps%", 0, 3),
    ("Architecte Java%", 0, 3),
    ("Consultant informatique%", 0, 4),
    ("Product Owner technique%", 0, 4),
    ("Formation Technicien Syst%", 0, 3),
    ("Alternance Technicien Informatique%", 0, 3),
    ("Conseiller clientèle%", 0, 2),
    ("TECHNICIEN ADMINISTRATIF%", 0, 2),
]


def main() -> None:
    a = argparse.ArgumentParser(description="Rejoue la notation sur le jeu de référence")
    a.add_argument("--profil", type=int, default=1)
    args = a.parse_args()
    conformes = total = 0
    for motif, mini, maxi in REFERENCE:
        with session() as conn:
            r = conn.execute(
                "SELECT o.id, o.title FROM offers AS o JOIN matches AS m "
                "ON m.offer_id = o.id AND m.profile_id = ? WHERE o.title LIKE ? LIMIT 1",
                (args.profil, motif)).fetchone()
        if r is None:
            print(f"  ?          {motif} : introuvable")
            continue
        facteurs, explication, _ = evaluer(r["id"], args.profil)
        note = note_finale(facteurs)
        bon = mini <= note <= maxi
        conformes += bon
        total += 1
        f = facteurs
        print(f"{'✓' if bon else '✗'} {note:>2}/10 (attendu {mini}-{maxi})  {r['title'][:40]:<40}  "
              f"mét {f['metier']} niv {f['niveau']} comp {f['competences']} ctr {f['contrat']}")
        if not bon:
            print(f"      → {explication}")
    print(f"\n{conformes}/{total} conformes")


if __name__ == "__main__":
    main()
