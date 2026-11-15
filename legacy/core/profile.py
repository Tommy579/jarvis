"""
Profil utilisateur persistant d'OMI.
Se remplit automatiquement au fur et à mesure des observations.
Stocké dans omi_profile.json à côté de main.py.
"""

import copy
import json
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

PROFILE_PATH = Path(__file__).parent.parent / "omi_profile.json"
_PROFILE_LOCK = threading.RLock()
_LAST_FLUSH_TS = 0.0
_LAST_ACTIVE_APP = None

DEFAULT_PROFILE = {
    "_meta": {
        "created_at": None,
        "last_updated": None,
        "version": "1.1",
    },
    "identity": {
        "name": None,
        "language": "fr",
        "timezone": "Europe/Paris",
    },
    "work": {
        "current_projects": [],
        "tech_stack": [],
        "frequent_apps": [],
        "frequent_files": [],
        "work_style": None,
    },
    "habits": {
        "bad_habits": [],
        "posture_issues": [],
        "distraction_patterns": [],
        "focus_score": None,
    },
    "preferences": {
        "communication_style": None,
        "music_genres": [],
        "topics_of_interest": [],
    },
    "schedule": {
        "typical_start_time": None,
        "typical_end_time": None,
        "most_productive_hours": [],
    },
    "notes": [],
}


def _fresh_default_profile() -> dict:
    """Retourne une copie profonde de DEFAULT_PROFILE pour éviter tout partage de listes."""
    return copy.deepcopy(DEFAULT_PROFILE)


def load_profile() -> dict:
    """Charge le profil depuis le fichier JSON de manière thread-safe. Le crée s'il n'existe pas."""
    with _PROFILE_LOCK:
        if not PROFILE_PATH.exists():
            profile = _fresh_default_profile()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            profile["_meta"]["created_at"] = now_str
            profile["_meta"]["last_updated"] = now_str
            save_profile(profile)
            print(f"[Profil] Nouveau profil créé : {PROFILE_PATH}")
            return profile

        try:
            with open(PROFILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return _fresh_default_profile()
            return data
        except Exception as e:
            print(f"[Profil] Erreur lecture profil : {e} — profil par défaut utilisé")
            return _fresh_default_profile()


def save_profile(profile: dict) -> bool:
    """Sauvegarde le profil dans le fichier JSON de manière atomique et thread-safe."""
    with _PROFILE_LOCK:
        try:
            profile.setdefault("_meta", {})["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            tmp_path = PROFILE_PATH.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
            tmp_path.replace(PROFILE_PATH)
            return True
        except Exception as e:
            print(f"[Profil] Erreur sauvegarde : {e}")
            return False


def get_profile_summary() -> str:
    """Retourne un résumé textuel du profil pour injection dans les prompts."""
    profile = load_profile()
    lines = ["=== Profil utilisateur connu ==="]

    identity = profile.get("identity", {})
    if identity.get("name"):
        lines.append(f"Nom : {identity['name']}")

    work = profile.get("work", {})
    if work.get("current_projects"):
        lines.append(f"Projets en cours : {', '.join(work['current_projects'])}")
    if work.get("tech_stack"):
        lines.append(f"Stack technique : {', '.join(work['tech_stack'])}")
    if work.get("frequent_apps"):
        lines.append(f"Apps fréquentes : {', '.join(work['frequent_apps'])}")
    if work.get("work_style"):
        lines.append(f"Style de travail : {work['work_style']}")

    habits = profile.get("habits", {})
    if habits.get("bad_habits"):
        lines.append(f"Mauvaises habitudes connues : {', '.join(habits['bad_habits'])}")
    if habits.get("posture_issues"):
        lines.append(f"Problèmes de posture récurrents : {', '.join(habits['posture_issues'])}")
    if habits.get("distraction_patterns"):
        lines.append(f"Distractions fréquentes : {', '.join(habits['distraction_patterns'])}")

    prefs = profile.get("preferences", {})
    if prefs.get("communication_style"):
        lines.append(f"Style de communication préféré : {prefs['communication_style']}")
    if prefs.get("topics_of_interest"):
        lines.append(f"Centres d'intérêt : {', '.join(prefs['topics_of_interest'])}")

    schedule = profile.get("schedule", {})
    if schedule.get("typical_start_time"):
        lines.append(f"Heure habituelle de début : {schedule['typical_start_time']}")
    if schedule.get("most_productive_hours"):
        lines.append(f"Heures productives : {', '.join(schedule['most_productive_hours'])}")

    notes = profile.get("notes", [])
    if notes:
        recent_notes = notes[-3:]
        lines.append("Notes récentes :")
        for note in recent_notes:
            lines.append(f"  [{note.get('date', '?')}] {note.get('text', '')}")

    if len(lines) == 1:
        return ""

    lines.append("================================")
    return "\n".join(lines)


def record_active_app(app_name: str, force_flush: bool = False):
    """Enregistre l'application active avec limitation des écritures disque."""
    global _LAST_FLUSH_TS, _LAST_ACTIVE_APP
    if not app_name or app_name == "Unknown":
        return

    with _PROFILE_LOCK:
        try:
            now = time.time()
            # Éviter de réécrire le fichier toutes les 10s si l'application n'a pas changé
            if not force_flush and app_name == _LAST_ACTIVE_APP and (now - _LAST_FLUSH_TS) < 60.0:
                return

            profile = load_profile()
            log = profile.setdefault("activity_log", [])
            log.append({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "app": app_name,
            })
            # Conserver jusqu'à 3600 entrées (une journée complète) au lieu de 100
            profile["activity_log"] = log[-3600:]

            apps = [entry["app"] for entry in profile["activity_log"] if "app" in entry]
            common_apps = [app for app, _ in Counter(apps).most_common(5)]
            profile.setdefault("work", {})["frequent_apps"] = common_apps

            save_profile(profile)
            _LAST_FLUSH_TS = now
            _LAST_ACTIVE_APP = app_name
        except Exception as e:
            print(f"[Profil] Erreur record_active_app : {e}")


def generate_daily_summary():
    """Génère un résumé quotidien basé sur toutes les entrées de la journée courante."""
    with _PROFILE_LOCK:
        try:
            profile = load_profile()
            activity_log = profile.get("activity_log", [])
            if not activity_log:
                return

            today_prefix = datetime.now().strftime("%Y-%m-%d")
            todays_entries = [
                entry for entry in activity_log
                if str(entry.get("timestamp", "")).startswith(today_prefix)
            ]
            target_entries = todays_entries or activity_log

            apps = [entry["app"] for entry in target_entries if "app" in entry]
            app_counts = Counter(apps)

            summary_lines = [f"Résumé d'activité quotidienne ({today_prefix}) :"]
            for app, count in app_counts.most_common(5):
                minutes = max(1, int(count * 10 / 60))
                summary_lines.append(f"- {app} : environ {minutes} min ({count} échantillons)")

            summary_text = "\n".join(summary_lines)

            profile.setdefault("notes", []).append({
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "text": summary_text,
            })
            profile["notes"] = profile["notes"][-50:]
            save_profile(profile)
            print("[Profil] Résumé quotidien enregistré dans les notes.")
        except Exception as e:
            print(f"[Profil] Erreur génération résumé : {e}")
