#!/usr/bin/env python3
import codecs
import os
import sys
import traceback
from html import unescape
from typing import Any, Literal
from urllib.parse import urlparse

import regex as re

from . import auth, utils
from .community_downloader import get_post_channel_url
from .shared_imports import B, F, S
from .files import ConfigContainer # Import the new ConfigContainer

# Custom Exceptions for Config Validation
class ConfigValidationError(Exception):
    """Base class for configuration validation errors."""
    pass

class InvalidConfigValueError(ConfigValidationError):
    """Raised when a config setting has an invalid value."""
    pass

class MissingConfigSettingError(ConfigValidationError):
    """Raised when a required config setting is missing."""
    pass

##################################### VALIDATE VIDEO ID #####################################
def validate_video_id(video_url_or_id: str, silent: bool = False, pass_exception: bool = False, basicCheck: bool = False):
    youtube_video_link_regex = r"^\s*(?P<video_url>(?:(?:https?:)?\/\/)?(?:(?:www|m)\.)?(?:youtube\.com|youtu.be)(?:\/(?:[\w\-]+\?v=|embed\/|v\/)?))?(?P<video_id>[\w\-]{11})(?:(?(video_url)\S+|$))?\s*$"
    match = re.match(youtube_video_link_regex, video_url_or_id)
    if not match:
        if basicCheck: return False, None, None, None, None, None
        if not silent:
            # Simplified error printing for brevity in this context
            print(f"\n{B.RED}{F.BLACK}Invalid Video link or ID!{S.R}")
        return False, None, None, None, None, None

    possibleVideoID = match.group('video_id')
    if len(possibleVideoID) != 11 : # Ensure ID is 11 chars even in basicCheck
        if not silent and not basicCheck: print(f"\n{B.RED}{F.BLACK}Invalid Video ID length!{S.R}")
        return False, None, None, None, None, None
    if basicCheck:
        return True, possibleVideoID, None, None, None, None # Return possibleVideoID for basicCheck success

    # Full validation with API call
    try:
        result = auth.YOUTUBE.videos().list(
            part="snippet,id,statistics", id=possibleVideoID,
            fields='items(id,snippet(channelId,channelTitle,title),statistics(commentCount))' # Optimized fields
        ).execute()
        if not result.get('items'):
            if not silent: print(f"{F.RED}No info for Video ID: {possibleVideoID} (unavailable/deleted).{S.R}")
            return False, None, None, None, None, None

        item = result['items'][0]
        if possibleVideoID == item['id']:
            snippet = item['snippet']
            channelID = snippet['channelId']
            channelTitle = snippet["channelTitle"]
            videoTitle = unescape(snippet["title"])
            commentCount = item.get('statistics', {}).get('commentCount') # Graceful access
            if commentCount is None: # Comments disabled or not present
                if pass_exception: return True, possibleVideoID, videoTitle, "0", channelID, channelTitle
                if not silent: print(f"{F.YELLOW}Comments disabled or count unavailable for {possibleVideoID}.{S.R}")
                # Signal for main menu or specific handling by caller
                # Returning a distinct tuple to indicate this specific case, instead of "MainMenu" string for all fields
                return "COMMENTS_DISABLED", possibleVideoID, videoTitle, "0", channelID, channelTitle
            return True, possibleVideoID, videoTitle, str(commentCount), channelID, channelTitle
        if not silent: print("YouTube API returned mismatched video ID.")
        return False, None, None, None, None, None
    except Exception as e:
        if not silent: print(f"{F.RED}API Error validating video ID {possibleVideoID}: {e}{S.R}")
        return False, None, None, None, None, None

############################### VALIDATE COMMUNITY POST ID #################################
def validate_post_id(post_url: str):
    isolatedPostID = ""
    if "/post/" in post_url: startIndex = post_url.rindex("/") + 1; endIndex = len(post_url)
    elif "/channel/" in post_url and "/community?" in post_url and "lb=" in post_url: startIndex = post_url.rindex("lb=") + 3; endIndex = len(post_url)
    else: isolatedPostID = post_url
    if not isolatedPostID: # Check if isolatedPostID is empty after initial logic
        try: # This try-except seems to be for the case where isolatedPostID was not set directly
            if startIndex < endIndex <= len(post_url): isolatedPostID = post_url[startIndex:endIndex]
        except NameError: # startIndex/endIndex might not be defined
             return False, None, None, None, None
        except Exception: return False, None, None, None, None
    if (len(isolatedPostID) == 26 or len(isolatedPostID) == 36) and isolatedPostID.startswith("Ug"):
        validatedPostUrl = "https://www.youtube.com/post/" + isolatedPostID
        try:
            postOwnerURL = get_post_channel_url(isolatedPostID) # API call
            if not postOwnerURL: return False, None, None, None, None
            valid, postOwnerID, postOwnerUsername = validate_channel_id(postOwnerURL) # API call
            return valid, isolatedPostID, validatedPostUrl, postOwnerID, postOwnerUsername
        except Exception as e:
            print(f"Error during community post validation: {e}")
            return False, None, None, None, None
    return False, None, None, None, None

##################################### VALIDATE CHANNEL ID ##################################
def validate_channel_id(inputted_channel: str):
    # Simplified version for brevity, actual API calls are costly for repeated validation
    # Prefer basic format checks here if full validation isn't strictly needed in all contexts
    # This function is called by validate_post_id, creating nested API calls.
    # For config validation, a basic format check is usually enough.
    # The original function makes API calls which might not be desired during config validation.
    # For now, retaining original logic but noting the performance/quota implication.
    isolatedChannelID = "Invalid"; inputted_channel = inputted_channel.strip()
    notChannelList = ['?v=', 'v=', '/embed/', '/vi/', '?feature=', '/v/', '/e/']
    if validate_video_id(inputted_channel, silent=True, basicCheck=True)[0]:
        print(f"{F.RED}Invalid Channel: Looks like a Video Link.{S.R}"); return False, None, None

    # Basic UCID check first
    if re.match(r'^UC[0-9A-Za-z_-]{22}$', inputted_channel): # Standard Channel ID format (UC + 22 chars)
        isolatedChannelID = inputted_channel
    # Add more specific regex for other formats if needed, or rely on API for complex ones
    elif "/channel/" in inputted_channel:
        try: isolatedChannelID = inputted_channel.split('/channel/')[1].split('/')[0].split('?')[0]
        except: pass
    elif inputted_channel.startswith("@"): # Basic handle check
        if re.match(r"@[a-zA-Z0-9_.-]{3,30}", inputted_channel):
             # API call needed to resolve handle to UCID; skip for pure config validation if possible
             # For now, we'll assume this needs an API call as per original logic
            try:
                response = auth.YOUTUBE.search().list(part="snippet", q=inputted_channel, maxResults=1, type="channel").execute()
                if response.get("items"): isolatedChannelID = response.get("items")[0]["snippet"]["channelId"]
                else: print(f"{F.RED}No channel for handle {inputted_channel}.{S.R}"); return False, None, None
            except Exception as e: print(f"{F.RED}API error for handle {inputted_channel}: {e}{S.R}"); return False, None, None
        else: print(f"{F.RED}Invalid handle format.{S.R}"); return False, None, None
    # Add /c/ and /user/ parsing if strictly needed for config validation without API
    # else: print(f"{F.RED}Unrecognized channel format.{S.R}"); return False, None, None

    if not (len(isolatedChannelID) == 24 and isolatedChannelID.startswith("UC")):
        # If it wasn't a direct UCID, and other parsing failed or needs API
        # print(f"{F.RED}Could not isolate a valid Channel ID from '{inputted_channel}'.{S.R}")
        return False, None, None # Fallback if no specific format matched or API call failed

    # Final verification with API if we have a candidate UCID
    try:
        response = auth.YOUTUBE.channels().list(part="snippet", id=isolatedChannelID).execute()
        if response.get('items'): return True, isolatedChannelID, response['items'][0]['snippet']['title']
        print(f"{F.RED}Channel ID {isolatedChannelID} not found via API.{S.R}")
    except Exception as e:
        print(f"{F.RED}API error validating Channel ID {isolatedChannelID}: {e}{S.R}")
    return False, None, None


############################ Validate Regex Input #############################
def validate_regex(regex_from_user: str):
    try: re.compile(regex_from_user); return True, regex_from_user
    except re.error:
        try: re.compile(re.escape(regex_from_user)); return True, re.escape(regex_from_user)
        except re.error: return False, None

############################# VALIDATE CONFIG SETTINGS (NEW) #############################
def validate_config_settings(cfg: ConfigContainer):
    print("\nValidating Config Settings...")
    print("-----------------------------------------------------\n")

    def err(setting_path: str, value: Any, message: str, advice: str = ""):
        full_message = f"{B.RED}{F.WHITE}ERROR!{S.R} Invalid value for '{setting_path}': '{value}'. {message} {advice}"
        raise InvalidConfigValueError(full_message)

    # INFO
    if not isinstance(cfg.info.config_version, int) or cfg.info.config_version < 0: err("info.config_version", cfg.info.config_version, "Must be a non-negative integer.")
    if not (isinstance(cfg.info.use_this_config, bool) or (isinstance(cfg.info.use_this_config, str) and cfg.info.use_this_config.lower() == 'ask')): err("info.use_this_config", cfg.info.use_this_config, "Must be True, False, or 'ask'.")
    if not isinstance(cfg.info.this_config_description, str): err("info.this_config_description", cfg.info.this_config_description, "Must be a string.")

    # PATHS
    for p_attr, p_val in [("log_path", cfg.paths.log_path), ("configs_path", cfg.paths.configs_path)]:
        if not isinstance(p_val, str) or not p_val.strip(): err(f"paths.{p_attr}", p_val, "Must be a non-empty string for a directory path.")
        # Directory existence/creation is handled by load_config_orchestrator

    # GENERAL
    if cfg.general.your_channel_id.lower() != 'ask':
        is_valid_ch_id, _, _ = validate_channel_id(cfg.general.your_channel_id) # This can make API calls
        if not is_valid_ch_id: err("general.your_channel_id", cfg.general.your_channel_id, "Invalid Channel ID/URL. If using a name/handle, ensure it's correct or use the direct Channel ID (starts with UC).")
    if cfg.general.release_channel not in ['all', 'stable']: err("general.release_channel", cfg.general.release_channel, "Must be 'all' or 'stable'.")

    # SCAN_MODES
    valid_scan_modes = ['ask', 'chosenvideos', 'recentvideos', 'entirechannel', 'communitypost', 'recentcommunityposts']
    if cfg.scan_modes.scan_mode not in valid_scan_modes: err("scan_modes.scan_mode", cfg.scan_modes.scan_mode, f"Invalid. Valid: {valid_scan_modes}")
    if cfg.scan_modes.max_comments != 'ask' and (not isinstance(cfg.scan_modes.max_comments, int) or cfg.scan_modes.max_comments <= 0): err("scan_modes.max_comments", cfg.scan_modes.max_comments, "Must be 'ask' or a positive integer.")
    if cfg.scan_modes.videos_to_scan.lower() != 'ask':
        try: video_list = utils.string_to_list(cfg.scan_modes.videos_to_scan)
        except: err("scan_modes.videos_to_scan", cfg.scan_modes.videos_to_scan, "Must be 'ask' or comma-separated video IDs/URLs.")
        if not video_list and cfg.scan_modes.videos_to_scan.strip(): err("scan_modes.videos_to_scan", cfg.scan_modes.videos_to_scan, "List is empty after parsing.")
        for v_item in video_list:
            is_valid_vid,vid_id_basic,_,_,_,_ = validate_video_id(v_item, basicCheck=True, silent=True) # Use basic check
            if not is_valid_vid or not vid_id_basic : err("scan_modes.videos_to_scan", v_item, "Contains invalid video ID/URL format.")
    if cfg.scan_modes.channel_to_scan.lower() not in ['ask', 'mine']:
        is_valid_ch_scan, _, _ = validate_channel_id(cfg.scan_modes.channel_to_scan) # API call
        if not is_valid_ch_scan: err("scan_modes.channel_to_scan", cfg.scan_modes.channel_to_scan, "Invalid Channel ID/URL for scanning, or 'ask'/'mine'.")
    if cfg.scan_modes.recent_videos_amount != 'ask' and (not isinstance(cfg.scan_modes.recent_videos_amount, int) or cfg.scan_modes.recent_videos_amount <= 0): err("scan_modes.recent_videos_amount", cfg.scan_modes.recent_videos_amount, "Must be 'ask' or a positive integer.")

    # FILTER_MODES
    valid_filter_modes = ['ask', 'id', 'username', 'text', 'nameandtext', 'autoascii', 'autosmart', 'sensitivesmart']
    if cfg.filter_modes.filter_mode not in valid_filter_modes: err("filter_modes.filter_mode", cfg.filter_modes.filter_mode, f"Invalid. Valid: {valid_filter_modes}")
    valid_filter_submodes = ['ask', 'characters', 'strings', 'regex']
    if cfg.filter_modes.filter_submode not in valid_filter_submodes: err("filter_modes.filter_submode", cfg.filter_modes.filter_submode, f"Invalid. Valid: {valid_filter_submodes}")
    if cfg.filter_modes.channel_ids_to_filter.lower() != 'ask':
        try: ch_id_list = utils.string_to_list(cfg.filter_modes.channel_ids_to_filter)
        except: err("filter_modes.channel_ids_to_filter", cfg.filter_modes.channel_ids_to_filter, "Must be 'ask' or comma-separated channel IDs.")
        if not ch_id_list and cfg.filter_modes.channel_ids_to_filter.strip(): err("filter_modes.channel_ids_to_filter", cfg.filter_modes.channel_ids_to_filter, "List is empty after parsing.")
        for ch_id in ch_id_list:
            if not (len(ch_id) == 24 and ch_id.startswith("UC")): err("filter_modes.channel_ids_to_filter", ch_id, "Invalid Channel ID format (must be 24 chars, start with UC).")
    if cfg.filter_modes.autoascii_sensitivity not in ['ask', '1', '2', '3']: err("filter_modes.autoascii_sensitivity", cfg.filter_modes.autoascii_sensitivity, "Must be 'ask', '1', '2', or '3'.")
    if cfg.filter_modes.characters_to_filter.lower() != 'ask' and not utils.make_char_set(cfg.filter_modes.characters_to_filter, stripLettersNumbers=True, stripKeyboardSpecialChars=False, stripPunctuation=True):
         err("filter_modes.characters_to_filter", cfg.filter_modes.characters_to_filter, "No usable characters after stripping.")
    if cfg.filter_modes.strings_to_filter.lower() != 'ask' and not utils.string_to_list(cfg.filter_modes.strings_to_filter) and cfg.filter_modes.strings_to_filter.strip():
         err("filter_modes.strings_to_filter", cfg.filter_modes.strings_to_filter, "No strings provided or invalid list format.")
    if cfg.filter_modes.regex_to_filter.lower() != 'ask':
        is_valid_regex, _ = validate_regex(cfg.filter_modes.regex_to_filter)
        if not is_valid_regex: err("filter_modes.regex_to_filter", cfg.filter_modes.regex_to_filter, "Invalid regex pattern.")

    # DETECTION_TOGGLES
    if not (0.0 <= cfg.detection_toggles.levenshtein_distance <= 1.0): err("detection_toggles.levenshtein_distance", cfg.detection_toggles.levenshtein_distance, "Must be between 0.0 and 1.0.")
    if cfg.detection_toggles.minimum_duplicates <= 0: err("detection_toggles.minimum_duplicates", cfg.detection_toggles.minimum_duplicates, "Must be positive integer.")
    if cfg.detection_toggles.minimum_duplicate_length <= 0: err("detection_toggles.minimum_duplicate_length", cfg.detection_toggles.minimum_duplicate_length, "Must be positive integer.")
    if cfg.detection_toggles.stolen_minimum_text_length <= 0: err("detection_toggles.stolen_minimum_text_length", cfg.detection_toggles.stolen_minimum_text_length, "Must be positive integer.")
    for mode_list_str, setting_path_part in [(cfg.detection_toggles.duplicate_check_modes, "duplicate_check_modes"), (cfg.detection_toggles.stolen_comments_check_modes, "stolen_comments_check_modes")]:
        if mode_list_str.lower() != 'none':
            modes = utils.string_to_list(mode_list_str)
            for mode_item in modes:
                if mode_item not in valid_filter_modes or mode_item == 'ask': err(f"detection_toggles.{setting_path_part}", mode_item, f"Invalid mode. Valid modes are: {', '.join(m for m in valid_filter_modes if m != 'ask')}, or 'none'.")

    # ACTIONS
    if str(cfg.actions.enable_ban).lower() not in ['ask', 'true', 'false']: err("actions.enable_ban", cfg.actions.enable_ban, "Must be 'ask', True, or False.")
    if str(cfg.actions.remove_all_author_comments).lower() not in ['ask', 'true', 'false']: err("actions.remove_all_author_comments", cfg.actions.remove_all_author_comments, "Must be 'ask', True, or False.")
    if cfg.actions.removal_type not in ['rejected', 'heldforreview', 'reportspam']: err("actions.removal_type", cfg.actions.removal_type, "Invalid. Must be 'rejected', 'heldforreview', or 'reportspam'.")
    if str(cfg.actions.whitelist_excluded).lower() not in ['ask', 'true', 'false']: err("actions.whitelist_excluded", cfg.actions.whitelist_excluded, "Must be 'ask', True, or False.")

    # LOGGING
    if str(cfg.logging.enable_logging).lower() not in ['ask', 'true', 'false']: err("logging.enable_logging", cfg.logging.enable_logging, "Must be 'ask', True, or False.")
    if cfg.logging.log_mode not in ['rtf', 'plaintext']: err("logging.log_mode", cfg.logging.log_mode, "Must be 'rtf' or 'plaintext'.")
    try: codecs.lookup(cfg.logging.json_encoding)
    except LookupError: err("logging.json_encoding", cfg.logging.json_encoding, "Invalid encoding.")
    if isinstance(cfg.logging.json_profile_picture, str) and cfg.logging.json_profile_picture.lower() not in ['false', 'default', 'medium', 'high']: err("logging.json_profile_picture", cfg.logging.json_profile_picture, "Must be False, 'default', 'medium', or 'high'.")
    if cfg.logging.quota_limit <=0: err("logging.quota_limit", cfg.logging.quota_limit, "Must be a positive integer.")

    print(f"{F.GREEN}Config validation successful.{S.R}")
    return True
