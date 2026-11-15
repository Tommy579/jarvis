"""Interface en ligne de commande d'OMI 2.0 (omi chat et omi eval)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[3]
load_dotenv(ROOT_DIR / ".env", override=False)
for p in (
    ROOT_DIR,
    ROOT_DIR / "packages" / "shared",
    ROOT_DIR / "packages" / "core",
    ROOT_DIR / "packages" / "desktop",
):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


async def _run_eval_cmd(save: bool = False) -> int:
    from evals.harness import EvalHarness

    harness = EvalHarness()
    results = await harness.run_all()
    passed = sum(1 for r in results if r.passed)
    for r in results:
        status = "OK" if r.passed else "ECHEC"
        print(f"[{status}] {r.scenario_id} ({r.category}) : {r.details}")
    print(f"\nTotal : {passed}/{len(results)} scenarios valides.")
    if save:
        out = harness.save_report(results, ROOT_DIR / "evals" / "latest.json")
        print(f"Rapport enregistre : {out}")
    return 0 if passed == len(results) else 1


async def _run_chat_cmd(model: str) -> int:
    from omi_core.runtime import build_runtime

    data_dir = Path(os.environ.get("OMI_DATA_DIR", str(ROOT_DIR / "omi-data"))).expanduser()
    try:
        runtime = build_runtime(
            data_dir=data_dir, config_dir=ROOT_DIR / "config", allow_offline=False
        )
    except RuntimeError as exc:
        print(f"Erreur : {exc}")
        return 1

    if model:
        runtime.router.set_role_model("agent", model)
    active = runtime.router.get_role_route("agent")
    print(f"OMI CLI pret (modele={active.provider}:{active.model}). Tapez /model <slug> ou /quit.")

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit"):
            return 0
        if line.startswith("/model "):
            target = line.split(maxsplit=1)[1].strip()
            route = runtime.router.set_role_model("agent", target)
            print(f"Modele actif : {route.provider}:{route.model}")
            continue
        emitted = False

        def _print_chunk(chunk: str) -> None:
            nonlocal emitted
            emitted = True
            print(chunk, end="", flush=True)

        res = await runtime.agent.run_turn(line, channel="cli", on_delta=_print_chunk)
        if not emitted and res.text:
            print(res.text, end="", flush=True)
        print()


def _get_db():
    from omi_core.memory.schema import MemoryDatabase
    data_dir = Path(os.environ.get("OMI_DATA_DIR", str(ROOT_DIR / "omi-data"))).expanduser()
    return MemoryDatabase(data_dir / "omi.db")


def _run_orders_cmd(args: argparse.Namespace) -> int:
    db = _get_db()
    action = args.action

    if action == "list":
        orders = db.list_orders(active_only=not args.all)
        if not orders:
            print("Aucun ordre enregistré.")
            return 0
        print(f"{'ID':<15} {'Actif':<8} {'Urgence':<8} {'Déclencheur':<30} {'Expiration':<20} Note")
        print("-" * 90)
        for o in orders:
            active_str = "Oui" if o.get("active") else "Non"
            trigger = o.get("text_match") or f"{o.get('source_glob')}:{o.get('event_type_glob')}"
            exp = o.get("expires_at") or "Indéterminée"
            print(f"{o.get('id'):<15} {active_str:<8} {o.get('min_urgency'):<8} {trigger[:28]:<30} {exp[:18]:<20} {o.get('note', '')}")
        return 0

    if action == "add":
        from omi_shared.notifications import NotificationOrder
        order = NotificationOrder(
            text_match=args.text,
            min_urgency=args.urgency,
            expires_at=args.expires,
            max_fires=args.max_fires,
            note=args.note or (f"Surveiller: {args.text}" if args.text else ""),
        )
        db.upsert_order(order.model_dump())
        print(f"Ordre créé : #{order.id} (Urgence {order.min_urgency}, déclencheur: {args.text or '*'})")
        return 0

    if action == "rm":
        ok = db.revoke_order(args.order_id)
        if ok:
            print(f"Ordre #{args.order_id} révoqué.")
            return 0
        print(f"Erreur : ordre #{args.order_id} introuvable.")
        return 1

    return 0


def _run_policy_cmd(args: argparse.Namespace) -> int:
    from omi_core.notifications.policy import PolicyStore
    db = _get_db()
    store = PolicyStore(db)

    if args.action == "show":
        p = store.get()
        print("=== Politique de notification Jarvis ===")
        print(f"- Statut global          : {'Actif' if p.enabled else 'Désactivé'}")
        print(f"- Heures calmes          : {p.quiet_start} à {p.quiet_end} (Urgence admise la nuit: niveau {p.quiet_allow_urgency})")
        print(f"- Appels autorisés       : {'Oui' if p.allow_calls else 'Non'}")
        print(f"- Appels extrême urgence : {'Oui' if p.allow_emergency_calls else 'Non'}")
        print(f"- Plafond d'appels       : {p.max_calls_per_day} appels/jour")
        print(f"- Échelle de canaux      : {' -> '.join(p.channel_ladder)}")
        return 0

    if args.action == "set":
        updates = {}
        if args.no_calls:
            updates["allow_calls"] = False
        if args.allow_calls:
            updates["allow_calls"] = True
        if args.calls_only_emergency:
            updates["allow_calls"] = False
            updates["allow_emergency_calls"] = True
        if args.quiet_start:
            updates["quiet_start"] = args.quiet_start
        if args.quiet_end:
            updates["quiet_end"] = args.quiet_end
        if args.max_calls:
            updates["max_calls_per_day"] = args.max_calls

        p = store.update(**updates)
        print("Politique mise à jour avec succès :")
        print(f"Appels={p.allow_calls}, ExtrêmeUrgence={p.allow_emergency_calls}, Nuit={p.quiet_start}-{p.quiet_end}")
        return 0

    return 0


async def _run_notify_test_cmd(args: argparse.Namespace) -> int:
    from omi_core.runtime import build_runtime
    from omi_shared.models import SystemEvent
    data_dir = Path(os.environ.get("OMI_DATA_DIR", str(ROOT_DIR / "omi-data"))).expanduser()
    runtime = build_runtime(data_dir=data_dir, config_dir=ROOT_DIR / "config", allow_offline=True)
    msg = args.message or "Test de notification Jarvis"
    channel = args.channel
    sender = runtime.channels.get(channel)
    if not sender:
        print(f"Canal '{channel}' non configuré ou introuvable dans runtime.channels.")
        print(f"Canaux disponibles : {list(runtime.channels.keys())}")
        return 1
    res = await sender.send(msg, urgency=3)
    print(f"Résultat envoi [{channel}] : {res}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omi", description="OMI 2.0 CLI (chat, eval, orders, policy, notify)")
    sub = parser.add_subparsers(dest="command", required=True)

    eval_p = sub.add_parser("eval", help="Lance le banc d'essai reproductible (omi eval)")
    eval_p.add_argument(
        "--save", action="store_true", help="Enregistre le rapport dans evals/latest.json"
    )

    chat_p = sub.add_parser("chat", help="Lance une session interactive ou un changement de modele")
    chat_p.add_argument(
        "--model", default="deepseek/deepseek-v4.1-flash-1017", help="Modele ou alias initial"
    )

    # Orders
    orders_p = sub.add_parser("orders", help="Gère les ordres de notification persistés")
    orders_sub = orders_p.add_subparsers(dest="action", required=True)
    list_p = orders_sub.add_parser("list", help="Liste les ordres")
    list_p.add_argument("--all", action="store_true", help="Inclure les ordres révoqués ou expirés")
    add_p = orders_sub.add_parser("add", help="Ajoute un ordre")
    add_p.add_argument("--text", required=True, help="Texte ou mot-clé à surveiller")
    add_p.add_argument("--urgency", type=int, default=2, help="Niveau d'urgence minimum (0-4)")
    add_p.add_argument("--expires", default=None, help="Date ISO 8601 d'expiration")
    add_p.add_argument("--max-fires", type=int, default=None, help="Nombre maximal de déclenchements")
    add_p.add_argument("--note", default="", help="Note descriptive")
    rm_p = orders_sub.add_parser("rm", help="Révoque un ordre")
    rm_p.add_argument("order_id", help="Identifiant de l'ordre à révoquer")

    # Policy
    policy_p = sub.add_parser("policy", help="Affiche ou modifie la politique de notification")
    policy_sub = policy_p.add_subparsers(dest="action", required=True)
    policy_sub.add_parser("show", help="Affiche la politique actuelle")
    set_p = policy_sub.add_parser("set", help="Modifie la politique")
    set_p.add_argument("--no-calls", action="store_true", help="Désactive les appels vocaux")
    set_p.add_argument("--allow-calls", action="store_true", help="Active les appels vocaux")
    set_p.add_argument("--calls-only-emergency", action="store_true", help="Autorise uniquement les appels de niveau 4")
    set_p.add_argument("--quiet-start", help="Début des heures calmes (ex. 22:00)")
    set_p.add_argument("--quiet-end", help="Fin des heures calmes (ex. 08:00)")
    set_p.add_argument("--max-calls", type=int, help="Plafond quotidien d'appels")

    # Notify test
    notify_p = sub.add_parser("notify", help="Tests directs des canaux de notification")
    notify_sub = notify_p.add_subparsers(dest="action", required=True)
    test_p = notify_sub.add_parser("test", help="Teste l'envoi sur un canal")
    test_p.add_argument("--channel", default="telegram", help="Canal à tester (telegram)")
    test_p.add_argument("--message", default="Test de notification Jarvis", help="Message du test")

    args = parser.parse_args(argv)
    if args.command == "eval":
        return asyncio.run(_run_eval_cmd(save=bool(args.save)))
    if args.command == "chat":
        return asyncio.run(_run_chat_cmd(model=str(args.model)))
    if args.command == "orders":
        return _run_orders_cmd(args)
    if args.command == "policy":
        return _run_policy_cmd(args)
    if args.command == "notify" and args.action == "test":
        return asyncio.run(_run_notify_test_cmd(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
