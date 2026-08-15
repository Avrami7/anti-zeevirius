# Contrat d'API — interface web locale ANTI-ZEEVIRIUS

Document de référence figé. Backend et frontend sont développés en parallèle
contre ce contrat : ni l'un ni l'autre ne le modifie unilatéralement.

## Principes non négociables

1. **Zéro nouvelle dépendance.** Backend = `http.server` (stdlib) uniquement.
   Frontend = HTML/CSS/JS pur, **aucun CDN, aucun framework** (l'outil doit
   fonctionner sur une machine hors ligne, potentiellement infectée).
2. **Bind sur `127.0.0.1` exclusivement.** Jamais `0.0.0.0`. Port par défaut
   8777, `--port` pour changer.
3. **Jeton de session obligatoire.** Généré par `secrets.token_urlsafe(32)` au
   démarrage, injecté dans la page servie. Toute requête `/api/*` sans en-tête
   `X-AZ-Token` valide → `403`. Protège contre tout autre processus local.
4. **Aucune destruction sans double validation.** Toute action destructive est
   en deux temps : `dry_run: true` (défaut) retourne le plan sans rien toucher ;
   l'exécution réelle exige `dry_run: false` **et** un `confirm_token` renvoyé
   par l'appel dry-run. Un token est à usage unique et expire en 5 minutes.
5. **Dégradation propre hors Windows.** Aucune action ne doit crasher : si le
   module est indisponible (winreg, pywin32, schtasks), l'API répond
   `{"ok": false, "unavailable": true, "reason": "..."}` avec un HTTP 200.
   L'interface doit rester entièrement navigable sous Linux/macOS.

## Format d'échange

Requête : `POST /api/<action>`, corps JSON, en-tête `X-AZ-Token`.
Réponse : toujours HTTP 200 (sauf 403 jeton invalide / 404 action inconnue),
corps JSON de forme :

```json
{ "ok": true,  "data": { ... } }
{ "ok": false, "error": "message lisible", "unavailable": false }
```

Les opérations longues (scan de dossier, analyse disque) sont **asynchrones** :

```
POST /api/scan_directory   → { "ok": true, "data": { "job_id": "..." } }
GET  /api/job?id=<job_id>  → { "ok": true, "data": {
        "state": "running|done|error",
        "progress": 0.0-1.0, "current": "fichier en cours",
        "done": 42, "total": 512, "result": {...}, "error": null } }
POST /api/job_cancel       → { "ok": true }
```

## Actions (mappées sur les 30 options du menu CLI)

| Action | Corps | Module appelé | Destructif |
|---|---|---|---|
| `status` | — | agrégat | non |
| `scan_file` | `{path}` | HashScanner + YaraScanner + HeuristicScanner | non |
| `scan_directory` | `{path}` → job | `main.AntivirusEngine.scan_directory` | non |
| `realtime_start` | `{folders[]}` | `RealtimeMonitor.start` | non |
| `realtime_stop` | — | `RealtimeMonitor.stop` | non |
| `quarantine_list` | — | `QuarantineManager.list_quarantined` | non |
| `quarantine_restore` | `{id}` | `.restore_file` | non |
| `quarantine_delete` | `{id, dry_run, confirm_token}` | `.delete_permanently` | **oui** |
| `clean_full` | `{include_admin, dry_run, confirm_token}` | `TempCleaner.run_full_cleanup` | **oui** |
| `startup_list` | — | `StartupManager.list_*` | non |
| `startup_disable` | `{hive, key_path, name, dry_run, confirm_token}` | `.disable_registry_item` | **oui** (réversible) |
| `startup_restore` | `{hive, name}` | `.restore_registry_item` | non |
| `disk_analyze` | `{path}` → job | `DiskAnalyzer.analyze_disk` | non |
| `schedule_cleanup` | `{day, time}` | `TaskScheduler.create_weekly_cleanup_task` | non |
| `schedule_remove` | — | `.remove_scheduled_task` | non |
| `triage_scan` | `{path}` → job | `FileTriage.triage_directory` | non |
| `triage_apply` | `{files[], dry_run, confirm_token}` | `.move_to_staging` | **oui** (réversible) |
| `staging_list` | — | `.list_staging` | non |
| `staging_restore` | `{id}` | `.restore_from_staging` | non |
| `staging_purge` | `{older_than_days, dry_run, confirm_token}` | `.purge_staging` | **oui** |
| `shield_start` | `{folders[]}` | `RansomwareShield.deploy_canaries` | non |
| `shield_status` | — | `.check_canaries` + `.adaptive_threshold` | non |
| `shield_processes` | — | `.find_suspicious_processes` | non |
| `shield_stop` | — | `.remove_canaries` | non |
| `reputation_check` | `{path}` | `ReputationChecker.check_hash` | non |
| `reputation_configured` | — | `.is_configured` | non |
| `phishing_check` | `{url}` | `PhishingLinkChecker.check_url` | non |
| `organize_plan` | `{path, mode}` mode=category\|application\|importance | `FolderOrganizer.build_plan` | non |
| `organize_apply` | `{plan, dry_run, confirm_token}` | `.apply_plan` | **oui** (réversible) |
| `organize_move_folder` | `{source, target, dry_run, confirm_token}` | `.move_folder_into` | **oui** |
| `organize_least_used` | `{path, days, dry_run, confirm_token}` | `.organize_least_used` | **oui** (réversible) |
| `organize_sessions` | — | `.list_sessions` | non |
| `organize_undo` | `{session_id}` | `.undo_session` | non |
| `guardian_run` | `{folders[], dry_run, confirm_token}` → job | `SystemGuardian.run_full_pass` | **oui** (réversible) |
| `guardian_pending` | — | `.review_pending_deletions` | non |
| `guardian_confirm` | `{older_than_days, dry_run, confirm_token}` | `.confirm_permanent_deletion` | **oui** |
| `guardian_schedule` | `{time}` | `TaskScheduler.create_daily_guardian_task` | non |
| `guardian_unschedule` | — | `.remove_guardian_task` | non |
| `apps_list` | `{sort_by}` → job | `AppManager.list_all_sorted` | non |
| `apps_uninstall` | `{app, dry_run, confirm_token}` | `.uninstall` | **oui** |
| `apps_debloat` | `{apps[], dry_run, confirm_token}` | `.remove_known_bloatware` | **oui** |
| `residue_shortcuts` | — | `ResidueCleaner.find_orphaned_shortcuts` | non |
| `residue_registry` | — | `.find_orphaned_uninstall_entries` | non |
| `residue_folders` | — | `.find_candidate_orphaned_folders` | non |
| `residue_clean` | `{kind, items[], dry_run, confirm_token}` | `.stage_*` / `.backup_and_remove_*` | **oui** (réversible) |

## Réponse de `status` (alimente le tableau de bord)

```json
{ "ok": true, "data": {
  "platform": "Windows|Linux|Darwin", "is_admin": true,
  "modules": { "startup_manager": {"available": false, "reason": "winreg absent"}, ... },
  "signatures": { "hashes": 12, "yara_rules": 8, "last_update": "2026-08-15T..." },
  "quarantine_count": 3, "staging_count": 0,
  "realtime_active": false, "shield_active": false,
  "vt_configured": false
} }
```
