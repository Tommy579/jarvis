# Politique de sécurité

JARVIS est un assistant personnel qui tourne **localement** : il lit le presse-papiers, l'écran,
le microphone, exécute des commandes et garde une mémoire persistante. Les rapports de sécurité
sont donc traités en priorité.

## Versions prises en charge

| Version | Correctifs de sécurité |
| --- | --- |
| 2.0.x (dernière) | oui |
| < 2.0 | non |

## Signaler une vulnérabilité

**Ne créez pas d'issue publique.** Utilisez l'onglet *Security > Report a vulnerability*
(GitHub Security Advisories) du dépôt :

https://github.com/Tommy579/jarvis/security/advisories/new

Si les advisories ne sont pas disponibles, ouvrez une issue minimale demandant un canal privé,
sans détail exploitable.

Merci d'inclure :

- version et système (`jarvis doctor`, `GET /api/version`) ;
- fichier et ligne concernés, ou endpoint/outil ;
- scénario d'exploitation (qui attaque, avec quels droits) ;
- preuve minimale reproductible (commande, requête HTTP, charge utile) ;
- impact estimé et correctif suggéré si vous en avez un.

## Délais indicatifs

- accusé de réception : 7 jours ;
- première évaluation (valide / non valide, sévérité) : 30 jours ;
- correctif : selon la sévérité, sans engagement contractuel (projet personnel).

## Périmètre

Dans le périmètre : `packages/`, `web/`, `scripts/`, `install_linux.sh`, `install_windows.ps1`,
les workflows de release, l'image Docker et les unités `deploy/`.

Hors périmètre : vulnérabilités de dépendances déjà corrigées en amont (signalez-les plutôt via
`make security`), attaques nécessitant un accès physique à la machine déverrouillée, absence de
chiffrement du disque par l'utilisateur, et failles des fournisseurs LLM distants.

## Ce qui est déjà couvert

Voir `docs/security/carbon-report.md`, `docs/security/neon-report.md`,
`docs/security/opus-report.md` : autorisation API, approbations liées aux arguments, assainissement
de `run_command` et de l'environnement MCP, confinement du contenu non fiable, permissions des
données, durcissement systemd/Docker, intégrité des releases (SHA-256).