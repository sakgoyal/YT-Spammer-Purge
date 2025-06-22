#!/usr/bin/env python3
import hashlib
import io
import json
import os
import pathlib
import pickle
import sys
import tarfile
import time
import traceback
import zipfile
from configparser import ConfigParser
from datetime import datetime
from itertools import islice
from pathlib import Path
from random import randrange
from shutil import copyfile, move, rmtree # Retained shutil.copyfile for _backup_config_file
from typing import Any, Literal, Mapping, Optional

import regex as re
import requests
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.util import Retry
from packaging.version import Version as parse_version
from tqdm import tqdm

from dataclasses import dataclass, field
from .shared_imports import RESOURCES_FOLDER_NAME, B, F, S
from .utils import choice

# --- Configuration Data Classes ---
@dataclass
class ConfigInfo:
    config_version: int = 0
    use_this_config: Any = True # bool or 'ask'
    this_config_description: str = "Default Configuration"

@dataclass
class ConfigPaths:
    log_path: str = "logs"
    configs_path: str = "configs"

@dataclass
class ConfigGeneral:
    your_channel_id: str = "ask"
    auto_check_update: bool = True
    release_channel: str = "all" # 'all' or 'stable'
    skip_confirm_video: bool = False
    moderator_mode: bool = False
    auto_close: bool = False
    colors_enabled: bool = True
    encrypt_token_file: bool = False

@dataclass
class ConfigScanModes:
    scan_mode: str = "ask"
    max_comments: Any = "ask"
    videos_to_scan: str = "ask"
    channel_to_scan: str = "ask"
    recent_videos_amount: Any = "ask"

@dataclass
class ConfigFilterModes:
    filter_mode: str = "ask"
    filter_submode: str = "ask"
    channel_ids_to_filter: str = "ask"
    autoascii_sensitivity: str = "3"
    characters_to_filter: str = "ask"
    strings_to_filter: str = "ask"
    regex_to_filter: str = "ask"

@dataclass
class ConfigDetectionToggles:
    detect_link_spam: bool = True
    detect_sub_challenge_spam: bool = True
    detect_spam_threads: bool = True
    duplicate_check_modes: str = "autosmart, sensitivesmart"
    stolen_comments_check_modes: str = "autosmart, sensitivesmart"
    levenshtein_distance: float = 0.85
    minimum_duplicates: int = 3
    minimum_duplicate_length: int = 25
    stolen_minimum_text_length: int = 25
    fuzzy_stolen_comment_detection: bool = True

@dataclass
class ConfigActions:
    skip_deletion: bool = False
    delete_without_reviewing: bool = False
    enable_ban: Any = "ask"
    remove_all_author_comments: Any = "ask"
    removal_type: str = "heldforreview"
    whitelist_excluded: Any = "ask"
    check_deletion_success: bool = True

@dataclass
class ConfigLogging:
    enable_logging: Any = "ask"
    log_mode: str = "rtf"
    json_log: bool = False
    json_encoding: str = "utf-8"
    json_extra_data: bool = False
    json_log_all_comments: bool = False
    json_profile_picture: Any = False
    quota_limit: int = 9000

@dataclass
class ConfigContainer:
    info: ConfigInfo = field(default_factory=ConfigInfo)
    paths: ConfigPaths = field(default_factory=ConfigPaths)
    general: ConfigGeneral = field(default_factory=ConfigGeneral)
    scan_modes: ConfigScanModes = field(default_factory=ConfigScanModes)
    filter_modes: ConfigFilterModes = field(default_factory=ConfigFilterModes)
    detection_toggles: ConfigDetectionToggles = field(default_factory=ConfigDetectionToggles)
    actions: ConfigActions = field(default_factory=ConfigActions)
    logging: ConfigLogging = field(default_factory=ConfigLogging)
    source_file_path: Optional[str] = None
    is_default_config: bool = False
    using_fallback_defaults: bool = False
# --- End Configuration Data Classes ---

def _get_default_config_parser() -> ConfigParser:
    default_path = os.path.join(os.path.abspath("assets"), "default_config.ini")
    if hasattr(sys, '_MEIPASS'):
        default_path = os.path.join(sys._MEIPASS, "default_config.ini")
    if not os.path.exists(default_path):
        print(f"{F.RED}CRITICAL ERROR: default_config.ini not found at {default_path}{S.R}")
        sys.exit("Default configuration file is missing.")
    parser = ConfigParser()
    try:
        with open(default_path, 'r', encoding="utf-8") as configFile: config_data = configFile.read()
        config_data = config_data.replace("'", "").replace('"', "")
        parser.read_file(io.StringIO(config_data))
    except Exception as e:
        print(f"{F.RED}CRITICAL ERROR: Could not read or parse default_config.ini: {e}{S.R}")
        sys.exit("Failed to load default configuration.")
    return parser

def _populate_config_container_from_parser(parser: ConfigParser, app_config_version: int) -> ConfigContainer:
    cfg = ConfigContainer()
    def get_val(section: str, option: str, expected_type: type, default_value: Any):
        if parser.has_option(section, option):
            if expected_type == bool: return parser.getboolean(section, option)
            elif expected_type == int:
                val_str = parser.get(section, option)
                if val_str.lower() == 'ask': return 'ask'
                try: return int(val_str)
                except ValueError: return default_value
            elif expected_type == float:
                try: return parser.getfloat(section, option)
                except ValueError: return default_value
            else: return parser.get(section, option)
        return default_value

    cfg.info.config_version = get_val("info", "config_version", int, app_config_version)
    use_this_config_str = get_val("info", "use_this_config", str, "true")
    cfg.info.use_this_config = 'ask' if use_this_config_str.lower() == 'ask' else use_this_config_str.lower() == 'true'
    cfg.info.this_config_description = get_val("info", "this_config_description", str, cfg.info.this_config_description)

    for section_name, section_dataclass_instance, section_dataclass_type in [
        ("paths", cfg.paths, ConfigPaths), ("general", cfg.general, ConfigGeneral),
        ("scan_modes", cfg.scan_modes, ConfigScanModes), ("filter_modes", cfg.filter_modes, ConfigFilterModes),
        ("detection_toggles", cfg.detection_toggles, ConfigDetectionToggles),
        ("actions", cfg.actions, ConfigActions), ("logging", cfg.logging, ConfigLogging)
    ]:
        for field_name, field_type in section_dataclass_type.__annotations__.items():
            default = getattr(section_dataclass_type(), field_name)
            setattr(section_dataclass_instance, field_name, get_val(section_name, field_name, field_type, default))
    return cfg

def _find_config_file_path(user_config_filename: str = "SpamPurgeConfig.ini",
                             default_configs_foldername: str = "configs") -> Optional[str]:
    primary_config_cwd_path = os.path.abspath(user_config_filename)
    if os.path.exists(primary_config_cwd_path): return primary_config_cwd_path
    primary_config_in_configs_dir_path = os.path.join(os.path.abspath(default_configs_foldername), user_config_filename)
    if os.path.exists(primary_config_in_configs_dir_path): return primary_config_in_configs_dir_path
    return None

def _load_raw_config_from_path(path: str) -> Optional[ConfigParser]:
    if not os.path.exists(path): return None
    parser = ConfigParser()
    try:
        with open(path, 'r', encoding="utf-8") as configFile: config_data = configFile.read()
        config_data = config_data.replace("'", "").replace('"', "")
        parser.read_file(io.StringIO(config_data))
        return parser
    except Exception as e:
        print(f"{F.RED}Error reading or parsing config file at {path}: {e}{S.R}"); traceback.print_exc()
        return None

def _ensure_directory_exists(dir_path: str) -> bool:
    if dir_path and not os.path.isdir(dir_path): # Added check for empty dir_path
        try: os.makedirs(dir_path, exist_ok=True); print(f"Created directory: {dir_path}")
        except Exception as e: print(f"{F.RED}Error creating directory {dir_path}: {e}{S.R}"); return False
    return True

def _backup_config_file(user_config_path: str, existing_config_version: Any) -> Optional[str]:
    backup_folder_name = "User_Config_Backups"
    resources_path = os.path.abspath(RESOURCES_FOLDER_NAME)
    if not _ensure_directory_exists(resources_path): return None
    backup_destination_folder = os.path.join(resources_path, backup_folder_name)
    if not _ensure_directory_exists(backup_destination_folder): return None
    version_str = str(existing_config_version) if existing_config_version else "unknown"
    base_backup_name = f"{os.path.basename(user_config_path)}.backup_v{version_str}"
    backup_name_and_path = os.path.join(backup_destination_folder, base_backup_name)
    counter = 0
    original_backup_base_name = base_backup_name # Store original base for message
    while os.path.exists(backup_name_and_path):
        counter += 1; backup_name_and_path = os.path.join(backup_destination_folder, f"{os.path.splitext(original_backup_base_name)[0]}_{counter}{os.path.splitext(original_backup_base_name)[1]}")
    if counter > 0: print(f"Backup file {os.path.join(backup_destination_folder, original_backup_base_name)} already exists. Saving as {backup_name_and_path}")
    try:
        shutil.copyfile(user_config_path, backup_name_and_path)
        print(f"\nOld config file backed up to {F.CYAN}{backup_name_and_path}{S.R}")
        return backup_name_and_path
    except Exception as e:
        print(f"{F.RED}Error backing up config file {user_config_path} to {backup_name_and_path}: {e}{S.R}")
        try:
            fallback_base = f"{user_config_path}.backup_v{version_str}_local"
            fallback_backup_path = fallback_base; counter = 0
            original_fallback_base_name = fallback_base
            while os.path.exists(fallback_backup_path): counter +=1; fallback_backup_path = f"{os.path.splitext(original_fallback_base_name)[0]}_{counter}{os.path.splitext(original_fallback_base_name)[1]}"
            if counter > 0: print(f"Local backup {original_fallback_base_name} already exists. Saving as {fallback_backup_path}")
            shutil.move(user_config_path, fallback_backup_path)
            print(f"\nOld config file renamed locally to {F.CYAN}{fallback_backup_path}{S.R}.")
            return fallback_backup_path
        except Exception as e_move: print(f"{F.RED}Critical error: Could not copy or move old config file {user_config_path}: {e_move}{S.R}"); return None

def _perform_config_update_merge(user_config_parser: ConfigParser, default_config_parser: ConfigParser, user_config_path: str) -> bool:
    new_user_parser = ConfigParser()
    for section in default_config_parser.sections():
        if not new_user_parser.has_section(section): new_user_parser.add_section(section)
        for option, default_value in default_config_parser.items(section):
            new_user_parser.set(section, option, user_config_parser.get(section, option) if user_config_parser.has_option(section, option) else default_value)
    for section in user_config_parser.sections():
        if not new_user_parser.has_section(section): new_user_parser.add_section(section)
        for option, value in user_config_parser.items(section):
            if not new_user_parser.has_option(section, option): new_user_parser.set(section, option, value)
    try:
        with open(user_config_path, 'w', encoding="utf-8") as configfile: new_user_parser.write(configfile)
        print(f"Config file {F.CYAN}{user_config_path}{S.R} updated successfully with merged settings.")
        return True
    except Exception as e: print(f"{F.RED}Error writing updated config to {user_config_path}: {e}{S.R}"); return False

def _handle_config_update_if_needed(user_config_parser: ConfigParser, user_config_path: str, current_app_config_version: int) -> tuple[ConfigParser, bool]:
    updated = False; user_file_version = 0
    try: user_file_version = user_config_parser.getint("info", "config_version")
    except: print(f"{F.YELLOW}WARNING: 'config_version' not found or invalid in {user_config_path}. Assuming outdated.{S.R}")
    if user_file_version < current_app_config_version:
        print(f"\n{F.YELLOW}WARNING!{S.R} Your config file ({os.path.basename(user_config_path)}) is outdated (v{user_file_version}, current is v{current_app_config_version}).")
        if not choice("Proceed with config update?", bypass=False):
            print(f"{F.RED}Config update declined.{S.R}"); return user_config_parser, False
        if not _backup_config_file(user_config_path, user_file_version):
            print(f"{F.RED}Backup failed. Update aborted.{S.R}"); return user_config_parser, False
        default_parser = _get_default_config_parser()
        try: default_parser.set("info", "config_version", str(current_app_config_version))
        except: print(f"{F.RED}Critical Error: New default config versioning failed.{S.R}"); return user_config_parser, False
        if _perform_config_update_merge(user_config_parser, default_parser, user_config_path):
            reloaded_parser = _load_raw_config_from_path(user_config_path)
            if reloaded_parser: print(f"{F.GREEN}Config updated and reloaded.{S.R}"); return reloaded_parser, True
            else: print(f"{F.RED}Failed to reload updated config.{S.R}"); return user_config_parser, False
        else: print(f"{F.RED}Config update merge failed.{S.R}"); return user_config_parser, False
    return user_config_parser, updated

def _get_alternative_config_files(primary_config_filename: str = "SpamPurgeConfig.ini", configs_folder_name: str = "configs") -> list[tuple[str, str]]:
    alt_configs = []; config_num_expression = r'(?i)(?<=spampurgeconfig)(\d+?)(?=\.ini)'
    paths_to_check = [os.getcwd(), os.path.abspath(configs_folder_name)]
    processed_paths = set() # To avoid processing same file twice if CWD is configs_folder_name
    for check_path in paths_to_check:
        if not os.path.isdir(check_path): continue
        try:
            for file in os.listdir(check_path):
                full_path = os.path.abspath(os.path.join(check_path, file))
                if full_path in processed_paths: continue
                processed_paths.add(full_path)
                if file.lower().startswith("spampurgeconfig") and file.lower().endswith(".ini") and file.lower() != primary_config_filename.lower():
                    match = re.search(config_num_expression, file)
                    if match:
                        parser = _load_raw_config_from_path(full_path)
                        desc = parser.get("info", "this_config_description", fallback=f"Config {match.group(0)}") if parser else f"Config {match.group(0)} (could not read description)"
                        alt_configs.append((f"{match.group(0)}: {desc}", full_path))
        except Exception as e: print(f"Error listing alt configs in {check_path}: {e}")
    return alt_configs

def _prompt_user_for_config_choice(primary_config_path: Optional[str], alt_config_files: list[tuple[str,str]]) -> Optional[str]:
    print(f"\n{F.YELLOW}Multiple configuration options found.{S.R}")
    options: dict[str, Optional[str]] = {} ; current_opt_num = 1
    if primary_config_path:
        print(f"  {F.LIGHTCYAN_EX}{current_opt_num}{S.R}: Use primary config ({os.path.basename(primary_config_path)})")
        options[str(current_opt_num)] = primary_config_path; current_opt_num += 1
    for desc, path in alt_config_files:
        print(f"  {F.LIGHTCYAN_EX}{current_opt_num}{S.R}: {desc} ({os.path.basename(path)})")
        options[str(current_opt_num)] = path; current_opt_num +=1
    print(f"  {F.LIGHTCYAN_EX}D{S.R}: Use default settings (no custom config).")
    options['d'] = "DEFAULT_FALLBACK"
    print(f"  {F.LIGHTCYAN_EX}N{S.R}: Create a new numbered config file.")
    options['n'] = "CREATE_NEW_NUMBERED"
    while True:
        user_choice_str = input(f"\nChoose (1-{current_opt_num-1}, D, N, or X for main menu): ").strip().lower()
        if user_choice_str == 'x': return "MAIN_MENU"
        if user_choice_str in options: return options[user_choice_str]
        else: print(f"{F.RED}Invalid choice.{S.R}")

def create_new_config_file(target_config_path: str, app_config_version: int, description: Optional[str] = None) -> bool:
    default_parser = _get_default_config_parser()
    try:
        if not default_parser.has_section("info"): default_parser.add_section("info")
        default_parser.set("info", "config_version", str(app_config_version))
        default_parser.set("info", "this_config_description", description or f"Config {os.path.basename(target_config_path)} created {datetime.now().strftime('%Y-%m-%d')}")
        default_parser.set("info", "use_this_config", "True")
    except Exception as e: print(f"{F.RED}Error setting version/desc in new default: {e}{S.R}"); return False
    parent_dir = os.path.dirname(target_config_path)
    if parent_dir and not _ensure_directory_exists(parent_dir): return False
    try:
        with open(target_config_path, 'w', encoding="utf-8") as cf: default_parser.write(cf)
        print(f"{F.GREEN}Successfully created new config file: {target_config_path}{S.R}")
        return True
    except Exception as e: print(f"{F.RED}Error writing new config to {target_config_path}: {e}{S.R}"); return False

def _determine_new_numbered_config_path(primary_config_filename: str, configs_folder_name: str) -> str:
    config_num_expression = r'(?i)(?<=spampurgeconfig)(\d+?)(?=\.ini)'
    highest_num = 1
    # Check CWD and configs_folder for existing numbered configs
    paths_to_check = {os.getcwd(), os.path.abspath(configs_folder_name)}
    if os.path.exists(os.path.join(os.getcwd(), primary_config_filename)) or \
       os.path.exists(os.path.join(os.path.abspath(configs_folder_name), primary_config_filename)):
        highest_num = 1 # Start numbering from 2 if primary exists

    existing_nums = set()
    for check_path in paths_to_check:
        if not os.path.isdir(check_path): continue
        for file in os.listdir(check_path):
            if file.lower().startswith("spampurgeconfig") and file.lower().endswith(".ini"):
                match = re.search(config_num_expression, file)
                if match:
                    try: existing_nums.add(int(match.group(0)))
                    except ValueError: continue
    if existing_nums: highest_num = max(existing_nums)

    next_num = highest_num + 1
    target_dir = os.path.abspath(configs_folder_name)
    if not os.path.isdir(target_dir): _ensure_directory_exists(target_dir) # Try to create if not exists
    if not os.path.isdir(target_dir) : target_dir = os.getcwd() # Fallback to CWD if creation failed or not specified well

    return os.path.join(target_dir, f"SpamPurgeConfig{next_num}.ini")

def load_config_orchestrator(app_config_version: int, main_config_filename: str = "SpamPurgeConfig.ini",
                             default_configs_foldername: str = "configs", force_default: bool = False,
                             skip_user_prompts: bool = False, only_get_encrypt_setting: bool = False) -> ConfigContainer:
    asset_default_path = os.path.join(os.path.abspath("assets"), "default_config.ini")
    if hasattr(sys, '_MEIPASS'): asset_default_path = os.path.join(sys._MEIPASS, "default_config.ini")

    if force_default:
        default_parser = _get_default_config_parser()
        cfg = _populate_config_container_from_parser(default_parser, app_config_version)
        cfg.is_default_config = True; cfg.source_file_path = asset_default_path
        return cfg

    if only_get_encrypt_setting:
        user_cfg_path = _find_config_file_path(main_config_filename, default_configs_foldername)
        parser_to_use = _load_raw_config_from_path(user_cfg_path) if user_cfg_path else _get_default_config_parser()
        cfg = ConfigContainer() # Minimal container
        cfg.general.encrypt_token_file = parser_to_use.getboolean("general", "encrypt_token_file", fallback=ConfigGeneral.encrypt_token_file)
        cfg.source_file_path = user_cfg_path if user_cfg_path else asset_default_path
        return cfg

    user_config_to_load_path: Optional[str] = None
    found_primary_config_path = _find_config_file_path(main_config_filename, default_configs_foldername)

    if not skip_user_prompts:
        alt_configs = _get_alternative_config_files(main_config_filename, default_configs_foldername)
        should_prompt = True
        if found_primary_config_path and not alt_configs:
            temp_parser = _load_raw_config_from_path(found_primary_config_path)
            if temp_parser and temp_parser.get("info", "use_this_config", fallback="true").lower() == 'true':
                should_prompt = False
            elif temp_parser and temp_parser.get("info", "use_this_config", fallback="true").lower() == 'false':
                print(f"Primary config '{found_primary_config_path}' has 'use_this_config = False'.")
                found_primary_config_path = None # Will lead to default or creation prompt

        if should_prompt and (found_primary_config_path or alt_configs):
            chosen_signal = _prompt_user_for_config_choice(found_primary_config_path, alt_configs)
            if chosen_signal == "MAIN_MENU": raise SystemExit("User chose to return to main menu.")
            elif chosen_signal == "CREATE_NEW_NUMBERED":
                new_path = _determine_new_numbered_config_path(main_config_filename, default_configs_foldername)
                desc = input(f"Enter description for '{os.path.basename(new_path)}': ") or f"User config {os.path.basename(new_path)}"
                if create_new_config_file(new_path, app_config_version, description=desc): user_config_to_load_path = new_path
                else: print(f"{F.RED}Failed to create new config. Loading default.{S.R}") # Fall to default
            elif chosen_signal == "DEFAULT_FALLBACK": user_config_to_load_path = None
            else: user_config_to_load_path = chosen_signal
        elif not found_primary_config_path and not alt_configs: # No configs at all
            pass # user_config_to_load_path remains None
        else: # Only primary found and use_this_config is true
            user_config_to_load_path = found_primary_config_path
    else: # Skipping prompts
        user_config_to_load_path = found_primary_config_path

    final_parser: Optional[ConfigParser] = None
    cfg = ConfigContainer(is_default_config=True, source_file_path=asset_default_path)

    if user_config_to_load_path:
        raw_parser = _load_raw_config_from_path(user_config_to_load_path)
        if raw_parser:
            updated_parser, _ = _handle_config_update_if_needed(raw_parser, user_config_to_load_path, app_config_version)
            final_parser = updated_parser
            cfg.is_default_config = False; cfg.source_file_path = user_config_to_load_path
        else:
            print(f"{F.RED}Failed to load user config: {user_config_to_load_path}. Loading default.{S.R}")

    if not final_parser: # Default path
        final_parser = _get_default_config_parser()
        cfg.is_default_config = True; cfg.source_file_path = asset_default_path

        # Auto-create SpamPurgeConfig.ini if no user config was involved and not skipping prompts
        # And if chosen_signal was not explicitly to use default
        if user_config_to_load_path is None and \
           (not 'chosen_signal' in locals() or chosen_signal != "DEFAULT_FALLBACK") and \
           not skip_user_prompts:

            target_dir = os.path.abspath(default_configs_foldername)
            if not os.path.isdir(target_dir) : target_dir = os.getcwd() # Fallback to CWD for creation
            _ensure_directory_exists(target_dir) # Ensure it exists
            target_creation_path = os.path.join(target_dir, main_config_filename)

            if not os.path.exists(target_creation_path): # Check if it exists in final target dir
                 print(f"\nNo user config found. Creating default '{os.path.basename(target_creation_path)}' in '{os.path.dirname(target_creation_path)}'.")
                 if create_new_config_file(target_creation_path, app_config_version):
                    cfg.source_file_path = target_creation_path; cfg.is_default_config = False
                    final_parser = _load_raw_config_from_path(target_creation_path) # Reload
                    if not final_parser: # Should not happen
                        print(f"{F.RED}CRITICAL: Failed to reload newly created config. Using asset default.{S.R}")
                        final_parser = _get_default_config_parser(); cfg.is_default_config = True; cfg.source_file_path = asset_default_path
                 else:
                     print(f"{F.RED}Failed to create a default user config file at {target_creation_path}.{S.R}")


    if final_parser:
        cfg = _populate_config_container_from_parser(final_parser, app_config_version)
        # Restore metadata that might have been reset if _populate_... re-instantiates ConfigContainer
        cfg.source_file_path = getattr(cfg, 'source_file_path', None) or \
                               (user_config_to_load_path if user_config_to_load_path and os.path.exists(user_config_to_load_path) else asset_default_path)
        cfg.is_default_config = (cfg.source_file_path == asset_default_path)

    else: # Should be unreachable
        print(f"{F.RED}CRITICAL: Config loading failed. Using hardcoded defaults.{S.R}")
        cfg = ConfigContainer(using_fallback_defaults=True, is_default_config=True)
        cfg.info.config_version = app_config_version

    _ensure_directory_exists(os.path.abspath(cfg.paths.configs_path))
    _ensure_directory_exists(os.path.abspath(cfg.paths.log_path))

    return cfg

########################### Check Lists Updates ###########################
def check_lists_update(spamListDict: dict[str, Any], silentCheck: bool = False):
    SpamListFolder: str = spamListDict['Meta']['SpamListFolder']
    currentListVersion = spamListDict['Meta']['VersionInfo']['LatestLocalVersion']

    def update_last_checked():
        currentDate = datetime.today().strftime('%Y.%m.%d.%H.%M')
        spamListDict['Meta']['VersionInfo'].update({'LatestLocalVersion': latestRelease})
        spamListDict['Meta']['VersionInfo'].update({'LastChecked': currentDate})
        newJsonContents = json.dumps({'LatestRelease': latestRelease, 'LastChecked': currentDate})
        with open(spamListDict['Meta']['VersionInfo']['Path'], 'w', encoding="utf-8") as file:
            json.dump(newJsonContents, file, indent=4)

    if not silentCheck: print("\nChecking for updates to spam lists...\n")
    if not os.path.isdir(SpamListFolder):
        try: os.mkdir(SpamListFolder)
        except: print("Error: Could not create folder 'spam_lists'.")

    try:
        response = requests.get("https://api.github.com/repos/ThioJoe/YT-Spam-Domains-List/releases/latest")
        if response.status_code != 200:
            if response.status_code == 403:
                if not silentCheck: print(f"\n{B.RED}{F.WHITE}Error [U-4L]:{S.R} GitHub API rate limit reached.\n")
                return False if not silentCheck else spamListDict
            else:
                if not silentCheck: print(f"{B.RED}{F.WHITE}Error [U-3L]:{S.R} GitHub API error (status {response.status_code}).\n")
                return False if not silentCheck else spamListDict
        latestRelease = response.json()["tag_name"]
    except Exception as e:
        if not silentCheck: print(f"Error getting release info from GitHub: {e}")
        return False if not silentCheck else spamListDict

    if currentListVersion is None or (parse_version(latestRelease) > parse_version(currentListVersion)):
        print("\n>  A new spam list update is available. Downloading...")
        asset_info = response.json()["assets"][0]
        fileName = asset_info['name']
        total_size_in_bytes = asset_info['size']
        downloadFilePath = os.path.join(SpamListFolder, fileName)
        downloadURL = asset_info['browser_download_url']

        if not getRemoteFile(downloadURL, downloadFilePath, description="spam list zip file"): return False

        if os.path.exists(downloadFilePath) and os.stat(downloadFilePath).st_size == total_size_in_bytes:
            print("Extracting updated lists...")
            try:
                with zipfile.ZipFile(downloadFilePath, "r") as zip_ref: zip_ref.extractall(SpamListFolder)
                os.remove(downloadFilePath)
                update_last_checked()
                return spamListDict
            except Exception as e:
                if not silentCheck: print(f"\n> {F.RED}Error extracting spam lists: {e}{S.R} ")
                return False
        elif os.path.exists(downloadFilePath): # File exists but size mismatch
             os.remove(downloadFilePath)
             if not silentCheck: print(f" > {F.RED} File did not fully download. Please try again later.{S.R}\n")
        return False # Covers size mismatch or download failure
    else: # No update needed or available
        update_last_checked() # Still update the last checked time
        return spamListDict

############################# Check For Updated Filter Variables File ##############################
def get_current_filter_version(filterListDict: dict[str, Any]):
    filterFileName = filterListDict['Files']['FilterVariables']['FileName']
    filterFilePath = os.path.join(filterListDict['ResourcePath'], filterFileName)
    if os.path.isfile(filterFilePath): return get_list_file_version(filterFilePath)
    return None

def check_for_filter_update(filterListDict: dict[str, Any], silentCheck: bool = False):
    latestFilterURL = "https://raw.githubusercontent.com/ThioJoe/YT-Spammer-Purge/main/Scripts/filter_variables.py"
    filterFileName = filterListDict['Files']['FilterVariables']['FileName']
    filterFilePath = os.path.join(filterListDict['ResourcePath'], filterFileName)
    localVersion = filterListDict['LocalVersion']
    if localVersion is None: localVersion = "0.0.0" # Handle case where localVersion might be None

    try:
        http = urllib3.PoolManager(); filePartialData = http.request('GET', latestFilterURL, headers={'Range': 'bytes=0-100'})
        matchItem = re.search(r'(?<=\[)(.*?)(?=\])', filePartialData.data.decode('utf-8'))
        if not matchItem: return False, filterListDict
        latestFilterVersion = str(matchItem.group(0))
    except Exception as e:
        if not silentCheck: print(f"Error getting filter version from GitHub: {e}")
        return False, filterListDict

    if parse_version(localVersion) < parse_version(latestFilterVersion):
        print("\n>  A new filter variables update is available. Downloading...")
        backupFilePath = os.path.join(filterListDict['ResourcePath'], f"filter_variables.py.{localVersion}")
        try: copyfile(filterFilePath, os.path.abspath(backupFilePath)); print(f"\nOld filter file backed up to {backupFilePath}\n")
        except: print(f" > {F.RED}Error:{S.R} Could not backup old filter_variables.py."); # Continue without backup? Or return False?

        if getRemoteFile(latestFilterURL, filterFilePath, description="filter variables file"):
            filterListDict['LocalVersion'] = latestFilterVersion
            print(f"{F.LIGHTGREEN_EX}Filter variables file updated.{S.R}\n")
            return True, filterListDict
        else: return False, filterListDict
    return True, filterListDict # No update needed

############################# Check For App Update ##############################
def check_for_update(currentVersion: str | int, updateReleaseChannel: Literal['stable', 'all'], silentCheck: bool = False):
    isUpdateAvailable = False; print("\nGetting info about latest updates...")
    try:
        api_url = "https://api.github.com/repos/ThioJoe/YT-Spammer-Purge/releases"
        if updateReleaseChannel == "stable": api_url += "/latest"
        response = requests.get(api_url, timeout=10)
        if response.status_code != 200:
            if not silentCheck: print(f"\n{B.RED}{F.WHITE}Error [U-GitHub]:{S.R} GitHub API error (status {response.status_code}).\n")
            return None

        release_data = response.json()
        latest_release = release_data[0] if updateReleaseChannel == "all" and isinstance(release_data, list) else release_data
        latestVersion = latest_release["name"]
        isBeta = latest_release["prerelease"]

    except Exception as e:
        if not silentCheck: print(f"{B.RED}{F.WHITE}Error [Code U-1]:{S.R} Problem checking for updates: {e}\n")
        return None

    if parse_version(latestVersion) > parse_version(str(currentVersion)): # Ensure currentVersion is str for comparison
        isUpdateAvailable = "beta" if isBeta else True
        if not silentCheck:
            print("------------------------------------------------------------------------------------------")
            print(f" A {F.LIGHTGREEN_EX}{'beta ' if isBeta else ''}new version{S.R} is available! Visit {F.YELLOW}TJoe.io/latest{S.R}")
            print(f"   > Current: {currentVersion} | Latest: {F.LIGHTGREEN_EX}{latestVersion}{S.R}")
            if isBeta: print("(To stop beta releases, change 'release_channel' in config)")
            print("------------------------------------------------------------------------------------------")
            if choice("Update Now?"):
                if sys.platform == 'win32' or sys.platform == 'win64':
                    assets = latest_release["assets"]
                    exe_asset = next((a for a in assets if '.exe' in a['name'].lower()), None)
                    sha_asset = next((a for a in assets if '.sha256' in a['name'].lower()), None)

                    if not exe_asset: print(f"{F.RED}No .exe found in release.{S.R}"); return False

                    filedownload_url = exe_asset['browser_download_url']
                    # This is simplified, original had complex requests.get(..., stream=True)
                    # For the purpose of this example, we'll use the refactored getRemoteFile
                    # but the original code for EXE download was not using getRemoteFile
                    print(f"\n> {F.LIGHTCYAN_EX} Downloading Latest Version...{S.R}")
                    downloadFileName = exe_asset['name']
                    if os.path.exists(downloadFileName) and not choice(f"Overwrite {downloadFileName}?"): return False

                    # Manual download with tqdm for this specific case, as original did not use getRemoteFile here
                    try:
                        dl_response = requests.get(filedownload_url, stream=True, timeout=30)
                        dl_response.raise_for_status()
                        total_size = int(dl_response.headers.get('content-length', 0))
                        with open(downloadFileName, 'wb') as f, tqdm(
                            desc=f"{F.LIGHTGREEN_EX}Downloading {downloadFileName}{S.R}", total=total_size, unit='iB', unit_scale=True, unit_divisor=1024,
                            bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]', colour="green"
                        ) as bar:
                            for chunk in dl_response.iter_content(chunk_size=8192):
                                f.write(chunk)
                                bar.update(len(chunk))
                        print(f"\n>  {F.LIGHTCYAN_EX}Verifying Download Integrity...{S.R}")
                        if total_size != 0 and os.stat(downloadFileName).st_size != total_size:
                            print(f"{F.RED}Download size mismatch.{S.R}"); os.remove(downloadFileName); return False
                        if sha_asset:
                            sha_url = sha_asset['browser_download_url']
                            sha_response = requests.get(sha_url, timeout=10)
                            expected_hash = sha_response.text.split()[0].lower() # Assuming format 'hash filename'
                            with open(downloadFileName, 'rb') as f_check: downloaded_hash = hashlib.sha256(f_check.read()).hexdigest()
                            if downloaded_hash != expected_hash:
                                print(f"{F.RED}Hash mismatch.{S.R}"); os.remove(downloadFileName); return False
                        print(f"\n >  Download Completed: {F.LIGHTGREEN_EX}{downloadFileName}{S.R}")
                        sys.exit() # Exit for user to run new version
                    except Exception as e_dl:
                        print(f"{F.RED}Download/Verification Error: {e_dl}{S.R}"); return False
                elif os.name == "posix": # Simplified Linux/macOS update
                    print(f"{F.YELLOW}Automatic update for Linux/macOS not fully implemented in this refactor.{S.R}")
                    print(f"Please download manually from: https://github.com/ThioJoe/YT-Spammer-Purge/releases/tag/{latestVersion}")
                    return False # Or attempt original tar.gz logic if desired
                else: print(f"> {F.RED} Error:{S.R} Auto-updater unsupported for this OS."); return False
            else: return False # User chose not to update
        return isUpdateAvailable # For silent check
    elif not silentCheck: print(f"\nYou have the latest version: {F.LIGHTGREEN_EX}{currentVersion}{S.R}")
    return False

######################### Try To Get Remote File ##########################
def getRemoteFile(url: str, downloadFilePath: str, streamChoice: bool = True, silent: bool = False, headers: Mapping[str, str | bytes | None] = None, description: str = "file"):
    session = requests.Session()
    retry_strategy = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["HEAD", "GET", "OPTIONS"])
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter); session.mount("https://", adapter)
    try:
        response = session.get(url, headers=headers, timeout=10, stream=streamChoice)
        response.raise_for_status()
        with open(downloadFilePath, 'wb') as file:
            if streamChoice:
                for data in response.iter_content(1048576): file.write(data) # 1MiB block_size
            else: file.write(response.content)
        return True
    except requests.exceptions.RequestException as e:
        if not silent: traceback.print_exc(); print(f"{B.RED}{F.WHITE} Error {S.R} Fetching {url}: {e}")
        return False
    except Exception as e:
        if not silent: traceback.print_exc(); print(f"{B.RED}{F.WHITE} Unexpected Error {S.R} Downloading {description} from {url}: {e}")
        return False

############################# Ingest Other Files ##############################
def ingest_asset_file(fileName: str):
    def assetFilesPath(relative_path: str):
        if hasattr(sys, '_MEIPASS'): return os.path.join(sys._MEIPASS, relative_path)
        return os.path.join(os.path.abspath("assets"), relative_path)
    with open(assetFilesPath(fileName), 'r', encoding="utf-8") as file: data = file.readlines()
    return [line.strip().lower() for line in data if not line.strip().startswith('#')]

def copy_asset_file(fileName: str, destination: str):
    def assetFilesPath(relative_path):
        if hasattr(sys, '_MEIPASS'): return os.path.join(sys._MEIPASS, relative_path)
        return os.path.join(os.path.abspath("assets"), relative_path)
    copyfile(assetFilesPath(fileName), os.path.abspath(destination))

def copy_scripts_file(fileName: str, destination: str): # Note: This assumes it's running from a context where 'src/Scripts' is relevant
    def assetFilesPath(relative_path: str):
        if hasattr(sys, '_MEIPASS'): return os.path.join(sys._MEIPASS, "src", relative_path)
        return os.path.join(os.path.abspath("src/Scripts"), relative_path)
    copyfile(os.path.join(assetFilesPath(""+fileName)), os.path.abspath(destination))

def ingest_list_file(relativeFilePath: str, keepCase:bool=True):
    if not os.path.exists(relativeFilePath): return None
    with open(relativeFilePath, 'r+', encoding="utf-8") as listFile: # r+ to read and write
        listData = listFile.readlines()
        if listData and not listData[-1].endswith('\n'): # Ensure last line has newline
            listFile.write('\n')
            listData.append('\n') # Reflect change in listData if needed immediately
    return [line.strip() if keepCase else line.strip().lower() for line in listData if line.strip() and not line.strip().startswith('#')]

def get_list_file_version(relativeFilePath: str):
    if not os.path.exists(relativeFilePath): return None
    matchBetweenBrackets = r'(?<=\[)(.*?)(?=\])'
    try:
        with open(relativeFilePath, 'r', encoding="utf-8") as file:
            for line in islice(file, 0, 5):
                matchItem = re.search(matchBetweenBrackets, line)
                if matchItem: return str(matchItem.group(0))
    except: pass # Ignore errors reading version
    return None

def parse_comment_list(config_container: ConfigContainer, recovery: bool = False, removal: bool = False, returnFileName: bool = False):
    actionVerb = "recover" if recovery else "remove"
    actionNoun = "recovery" if recovery else "removal"
    validFile = False; manuallyEnter = False; listFileName = ""
    while not validFile and not manuallyEnter:
        print(f"\nEnter name of log file with comments to {actionVerb} (e.g., log.rtf)")
        listFileName_input = input(f"Or hit Enter to manually paste IDs: ").strip("\"'")
        if not listFileName_input: manuallyEnter = True; break

        potential_paths = [
            listFileName_input, f"{listFileName_input}.rtf", f"{listFileName_input}.txt",
            os.path.join(config_container.paths.log_path, listFileName_input),
            os.path.join(config_container.paths.log_path, f"{listFileName_input}.rtf"),
            os.path.join(config_container.paths.log_path, f"{listFileName_input}.txt")
        ]
        for p_path in potential_paths:
            if os.path.exists(p_path): listFileName = p_path; validFile = True; break

        if validFile:
            try:
                with open(listFileName, 'r', encoding="utf-8") as f: data = f.read()
            except: print(f"{F.RED}Error F-5:{S.R} Problem reading log file."); validFile = False # Force re-prompt or manual
        else:
            print(f"{F.RED}File not found.{S.R}")
            if not choice("Try again?"): manuallyEnter = True
            elif choice is None: return "MainMenu", None

    if manuallyEnter:
        data = str(input("Paste comma-separated ID list: "))
        if data.lower() == "x": return "MainMenu", None

    resultList = re.findall(r'Ug[A-Za-z0-9_\-]{20,}', data) # More specific regex for comment IDs
    if not resultList: print(f"{F.RED}No valid comment IDs found.{S.R}"); return "MainMenu", None

    print(f"{F.GREEN}Loaded {len(resultList)} comment IDs.{S.R}")
    if not returnFileName: return resultList, None
    return resultList, pathlib.Path(listFileName).stem if listFileName else f"Entered_List_{randrange(999)}"

def write_dict_pickle_file(dictToWrite, fileName: str, relativeFolderPath=RESOURCES_FOLDER_NAME, forceOverwrite: bool = False):
    _ensure_directory_exists(relativeFolderPath)
    fileNameWithPath = os.path.join(relativeFolderPath, fileName)
    if os.path.exists(fileNameWithPath) and not forceOverwrite:
        if not choice(f"File '{fileName}' exists. Overwrite?"):
            newFileName = input("Enter new file name (no extension): ") + ".save"
            fileNameWithPath = os.path.join(relativeFolderPath, newFileName)
    try:
        with open(fileNameWithPath, 'wb') as pickleFile: pickle.dump(dictToWrite, pickleFile)
        return True
    except Exception as e: print(f"Error writing pickle file {fileNameWithPath}: {e}"); return False

def read_dict_pickle_file(fileNameNoPath: str, relativeFolderPath=RESOURCES_FOLDER_NAME):
    fileNameWithPath = os.path.join(relativeFolderPath, fileNameNoPath)
    if not os.path.exists(fileNameWithPath): print(f"File '{fileNameNoPath}' not found."); return False
    try:
        with open(fileNameWithPath, 'rb') as pickleFile: return pickle.load(pickleFile)
    except Exception as e: print(f"Error reading pickle file {fileNameWithPath}: {e}"); return False

def try_remove_file(fileNameWithPath: str) -> bool:
    try: os.remove(fileNameWithPath); return True
    except OSError: print(f"{F.RED}ERROR:{S.R} Could not remove '{fileNameWithPath}'. Is it open?"); return False

def check_existing_save() -> list[str]:
    save_dir = Path(RESOURCES_FOLDER_NAME) / "Removal_List_Progress"
    return [f.name for f in save_dir.rglob("*.save") if f.is_file()] if save_dir.is_dir() else []

def save_compiled_regex_pickle(compiled_input, fileNameBase: str, latestListVersion: str, relativeFolderPath=Path(RESOURCES_FOLDER_NAME) / "Compiled_Regex"):
    _ensure_directory_exists(relativeFolderPath)
    fileName = f"{fileNameBase}_v{latestListVersion}.pickle"
    fileNameWithPath = Path(relativeFolderPath) / fileName
    try:
        with open(fileNameWithPath, 'wb') as pf: pickle.dump(compiled_input, pf)
        return True
    except Exception as e: print(f"Error saving precompiled regex {fileNameWithPath}: {e}"); return False

def read_compiled_regex_pickle(fileNameBase: str, latestListVersion: str, relativeFolderPath=Path(RESOURCES_FOLDER_NAME) / "Compiled_Regex"):
    if not Path(relativeFolderPath).is_dir(): _ensure_directory_exists(relativeFolderPath); return None
    expected_fn_part = f"{fileNameBase}_v{latestListVersion}.pickle"
    for file in Path(relativeFolderPath).iterdir():
        if file.name == expected_fn_part:
            try:
                with open(file, 'rb') as pf: return pickle.load(pf)
            except Exception as e: print(f"Error reading precompiled regex {file}: {e}"); return False
        elif file.name.startswith(fileNameBase) and file.name.endswith(".pickle"): # Old version
            try_remove_file(str(file))
    return None
