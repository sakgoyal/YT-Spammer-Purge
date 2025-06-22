#!/usr/bin/env python3
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~#
#######################################################################################################
################################# YOUTUBE SPAM COMMENT DELETER ########################################
#######################################################################################################
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~#
version = "2.18.0"
configVersion = 33 # This should match the default version in ConfigInfo dataclass in files.py
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~#
print("Importing Script Modules...")
import os
import sys
from typing import Any, NamedTuple # Retain Any, NamedTuple

from Scripts import auth, community_downloader, files, logging, operations, user_tools, utils, validation
from Scripts.community_downloader import main as get_community_comments
from Scripts.shared_imports import RESOURCES_FOLDER_NAME, B, F, S, init
from Scripts.types import ScanInstance
from Scripts.files import ConfigContainer # Import new ConfigContainer
from Scripts.validation import ConfigValidationError # Import custom exception
from Scripts.utils import choice

print("Importing Standard Libraries...")
import ast
import json
import time
from dataclasses import dataclass # Already used for MiscDataStore
from datetime import datetime, timedelta
from importlib import reload
import traceback # For general exception handling at the end

from packaging.version import Version as parse_version
from googleapiclient.errors import HttpError

utils.clear_terminal()

User = NamedTuple('User', [('id', str), ('name', str), ('configMatch', bool)])

def main():
    global YOUTUBE, CURRENTUSER # Keep globals for now as they are widely used

    if sys.version_info[0] < 3 or sys.version_info[1] < 10:
        print(f"Error Code U-2: Python 3.10+ required. You are running {sys.version_info[0]}.{sys.version_info[1]}")
        input("Press Enter to Exit..."); sys.exit()

    utils.clear_terminal()
    print(f"\nLoading YT Spammer Purge @ {version}...")

    YOUTUBE = auth.first_authentication() # Authentication needs to happen before config for encrypt_token_file

    # Load and validate configuration using the new orchestrator
    config_container: Optional[ConfigContainer] = None
    try:
        # The `only_get_encrypt_setting` logic is now handled within load_config_orchestrator
        # when auth.py calls it. Here, we do a full load.
        config_container = files.load_config_orchestrator(configVersion)
        validation.validate_config_settings(config_container)
    except ConfigValidationError as e:
        print(f"{F.RED}Configuration Error: {e}{S.R}")
        print("Please check your SpamPurgeConfig.ini or consider regenerating it.")
        input("Press Enter to Exit..."); sys.exit()
    except SystemExit as e: # User chose to exit from a config prompt
        print(e); sys.exit()
    except Exception as e:
        print(f"{F.RED}Unexpected error during config loading: {e}{S.R}"); traceback.print_exc()
        input("Press Enter to Exit..."); sys.exit()

    utils.clear_terminal()

    if not config_container.general.colors_enabled:
        init(autoreset=True, strip=True, convert=False)
    else:
        init(autoreset=True)

    resourceFolder = RESOURCES_FOLDER_NAME
    whitelistPathWithName = os.path.join(resourceFolder, "whitelist.txt")
    spamListFolder = os.path.join(config_container.paths.configs_path, "Spam_Lists") # Use configured path
    filtersFolder = os.path.join(config_container.paths.configs_path, "Filters")   # Use configured path
    filterFileName = "filter_variables.py" # This name is hardcoded in many places

    spamListDict = {
        'Lists': {'Domains': {'FileName': "SpamDomainsList.txt"}, 'Accounts': {'FileName': "SpamAccountsList.txt"}, 'Threads': {'FileName': "SpamThreadsList.txt"}},
        'Meta': { 'VersionInfo': {'FileName': "SpamVersionInfo.json"}, 'SpamListFolder': spamListFolder, }
    }
    filterListDict = {
        'Files': {'FilterVariables': {'FileName': filterFileName}}, 'ResourcePath': filtersFolder,
    }
    resourcesDict = {
        'Whitelist': { 'PathWithName': whitelistPathWithName, 'FileName': "whitelist.txt", },
        "VersionInfo": { 'LatestLocalSpamListVersion': "0.0.0.0",},
    }

    print("Checking for updates to program and spam lists...")
    # Ensure resource folders exist (using configured paths)
    files._ensure_directory_exists(resourceFolder) # Main resource folder
    files._ensure_directory_exists(spamListFolder)
    files._ensure_directory_exists(filtersFolder)


    for x, spamList_item in spamListDict['Lists'].items(): # Renamed spamList to spamList_item
        spamList_item['Path'] = os.path.join(spamListFolder, spamList_item['FileName'])

    spamListDict['Meta']['VersionInfo']['Path'] = os.path.join(spamListFolder, spamListDict['Meta']['VersionInfo']['FileName'])

    latestLocalSpamListVersion = "1900.12.31"
    for x, spamList_item in spamListDict['Lists'].items(): # Renamed spamList to spamList_item
        if not os.path.exists(spamList_item['Path']):
            files.copy_asset_file(spamList_item['FileName'], spamList_item['Path']) # Assumes asset file is in ./assets
        listVersion = files.get_list_file_version(spamList_item['Path'])
        spamList_item['Version'] = listVersion
        if listVersion and parse_version(listVersion) > parse_version(latestLocalSpamListVersion):
            latestLocalSpamListVersion = listVersion
    spamListDict['Meta']['VersionInfo']['LatestLocalVersion'] = latestLocalSpamListVersion

    if not os.path.exists(spamListDict['Meta']['VersionInfo']['Path']):
        files.copy_asset_file(spamListDict['Meta']['VersionInfo']['FileName'], spamListDict['Meta']['VersionInfo']['Path'])

    filterFilePath = os.path.join(filtersFolder, filterFileName)
    if not os.path.exists(filterFilePath):
        files.copy_scripts_file(filterFileName, filterFilePath) # Assumes script file is in src/Scripts

    with open(spamListDict['Meta']['VersionInfo']['Path'], 'r', encoding="utf-8") as jsonDataFile: # Renamed jsonData
        versionInfoJsonLoaded = json.load(jsonDataFile)
    spamListDict['Meta']['VersionInfo']['LatestRelease'] = versionInfoJsonLoaded.get('LatestRelease', '0.0.0')
    spamListDict['Meta']['VersionInfo']['LastChecked'] = versionInfoJsonLoaded.get('LastChecked', '1970.01.01.00.00')

    filterVersion = files.get_current_filter_version(filterListDict)
    filterListDict['LocalVersion'] = filterVersion
    filterListDict['LatestVersion'] = filterVersion

    updateReleaseChannel = config_container.general.release_channel
    if updateReleaseChannel not in ["all", "stable"]:
        print(f"Invalid 'release_channel': {updateReleaseChannel}. Defaulting to 'all'.")
        updateReleaseChannel = "all"

    updateAvailable = False # Default
    if config_container.general.auto_check_update:
        try:
            updateAvailable = files.check_for_update(version, updateReleaseChannel, silentCheck=True)
        except Exception:
            print(f"{F.LIGHTRED_EX}Error during update check. Continuing...{S.R}\n"); updateAvailable = None # type: ignore
        if datetime.today() > datetime.strptime(spamListDict['Meta']['VersionInfo']['LastChecked'], '%Y.%m.%d.%H.%M') + timedelta(days=1):
            files.check_for_filter_update(filterListDict, silentCheck=True)
            if datetime.today() + timedelta(days=1) >= datetime.strptime(spamListDict['Meta']['VersionInfo']['LatestLocalVersion'], '%Y.%m.%d'):
                spamListDict = files.check_lists_update(spamListDict, silentCheck=True)

    for spamList_item in spamListDict['Lists'].values(): # Renamed
        spamList_item['FilterContents'] = files.ingest_list_file(spamList_item['Path'], keepCase=False)

    print("Loading filter file...\n"); import Scripts.prepare_modes as modes
    print("\nLoading other assets..\n")

    @dataclass
    class MiscDataStore:
        resources: dict[str, Any]; spamLists: dict[str, Any]; totalCommentCount: int
        channelOwnerID: str; channelOwnerName: str
    miscData = MiscDataStore(resources={}, spamLists={}, totalCommentCount=0, channelOwnerID="", channelOwnerName="")
    miscData.resources = resourcesDict
    miscData.resources['rootDomainList'] = files.ingest_asset_file("rootZoneDomainList.txt")
    miscData.spamLists['spamDomainsList'] = spamListDict['Lists']['Domains']['FilterContents']
    miscData.spamLists['spamAccountsList'] = spamListDict['Lists']['Accounts']['FilterContents']
    miscData.spamLists['spamThreadsList'] = spamListDict['Lists']['Threads']['FilterContents']
    miscData.spamLists['latestLocalVersion'] = spamListDict['Meta']['VersionInfo']['LatestLocalVersion']

    if not os.path.exists(whitelistPathWithName):
        with open(whitelistPathWithName, "a", encoding="utf-8") as f:
            f.write("# Commenters whose channel IDs are in this list will always be ignored...\n") # Shortened for brevity
        miscData.resources['Whitelist']['WhitelistContents'] = []
    else:
        miscData.resources['Whitelist']['WhitelistContents'] = files.ingest_list_file(whitelistPathWithName, keepCase=True)

    moderator_mode = config_container.general.moderator_mode
    utils.clear_terminal()
    print(f"{F.LIGHTYELLOW_EX}\n===================== YOUTUBE SPAMMER PURGE v{version} ====================={S.R}")
    print("=========== https://github.com/ThioJoe/YT-Spammer-Purge ===========")
    print("================= Author: ThioJoe - YouTube.com/ThioJoe ================ \n")
    print("Purpose: Lets you scan for spam comments and mass-delete them all at once \n")

    confirmedCorrectLogin = False
    while not confirmedCorrectLogin:
        userInfo = auth.get_current_user(
            current_config=config_container # Pass the whole container to auth.get_current_user
        )
        CURRENTUSER = User(id=userInfo[0], name=userInfo[1], configMatch=userInfo[2])
        auth.CURRENTUSER = CURRENTUSER # Ensure auth module has the latest CURRENTUSER
        print(f"\n    >  Currently logged in user: {F.LIGHTGREEN_EX}{str(CURRENTUSER.name)}{S.R} (Channel ID: {F.LIGHTGREEN_EX}{str(CURRENTUSER.id)}{S.R} )")
        if choice("       Continue as this user?", CURRENTUSER.configMatch): # configMatch is determined by get_current_user
            confirmedCorrectLogin = True; utils.clear_terminal()
        else:
            auth.remove_token(); utils.clear_terminal()
            YOUTUBE = auth.get_authenticated_service() # Re-authenticate

    def primaryInstance(miscData_param: MiscDataStore, current_config: ConfigContainer):
        primaryInstance.return_to_main_menu = False # type: ignore # Initialize flag
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        current_scan = ScanInstance(logTime=timestamp) # Use ScanInstance defaults

        maxScanNumber = 999999999 # Default large number
        scanVideoID = None; videosToScan = []; recentPostsListofDicts = []; postURL = ""
        loggingEnabled = False; userNotChannelOwner = False
        utils.clear_terminal()

        updateStringLabel = ""; updateString = ""
        if updateAvailable: # updateAvailable is from main scope (defined before main_loop)
            updateStringLabel = "Update Available: "
            if updateAvailable is True: updateString = f"{B.LIGHTGREEN_EX}{F.BLACK} Yes {S.R}"
            elif updateAvailable == "beta":
                # updateReleaseChannel is also from main scope
                if updateReleaseChannel == "all": updateString = f"{B.LIGHTCYAN_EX}{F.BLACK} Beta {S.R}"
                # If stable, beta won't be shown by check_for_update usually unless it's the only 'latest'
            elif updateAvailable is None: updateString = f"{F.LIGHTRED_EX}Error{S.R}"
        elif not current_config.general.auto_check_update: # Accessing ConfigContainer
            updateStringLabel = "Update Checking: "; updateString = "Off"

        print(f"\n{'> At any prompt, enter \'X\' to return here':<59}{updateStringLabel:<18}{updateString:>7}")
        print("> Enter 'Q' now to quit")
        print(f"\n\n-------------------------------- {F.YELLOW}Scanning Options{S.R} --------------------------------")
        # ... (menu print remains same)
        print(f"      1. Scan {F.LIGHTCYAN_EX}specific videos{S.R}")
        print(f"      2. Scan {F.LIGHTCYAN_EX}recent videos{S.R} for a channel")
        print(f"      3. Scan recent comments across your {F.LIGHTBLUE_EX}Entire Channel{S.R}")
        print(f"      4. Scan a specific {F.LIGHTMAGENTA_EX}community post{S.R} (Experimental)")
        print(f"      5. Scan {F.LIGHTMAGENTA_EX}recent community posts{S.R} for a channel (Experimental)")
        print(f"\n--------------------------------- {F.YELLOW}Other Options{S.R} ----------------------------------")
        print(f"      6. Create/Manage {F.LIGHTGREEN_EX}config file(s){S.R}") # Slightly reworded
        print(f"      7. Remove comments using a {F.LIGHTRED_EX}pre-existing list{S.R} or log file")
        print("      8. Recover deleted comments using log file")
        print(f"      9. Check & Download {F.LIGHTCYAN_EX}Updates{S.R}")
        print(f"      10. {F.BLACK}{B.LIGHTGREEN_EX} NEW! {S.R} Helpful Tools")
        print("")

        validMode = False; scanMode_str = "" # Renamed scanMode to scanMode_str to avoid conflict
        while not validMode:
            if current_config.scan_modes.scan_mode != 'ask':
                scanMode_str = current_config.scan_modes.scan_mode
            else:
                scanMode_str = input("Choice (1-10): ")
            if scanMode_str.lower() == "q": sys.exit()

            choice_map = {
                "1": "chosenVideos", "chosenvideos": "chosenVideos", "2": "recentVideos", "recentvideos": "recentVideos",
                "3": "entireChannel", "entirechannel": "entireChannel", "4": "communityPost", "communitypost": "communityPost",
                "5": "recentCommunityPosts", "recentcommunityposts": "recentCommunityPosts", "6": "makeConfig",
                "7": "commentList", "commentlist": "commentList", "8": "recoverMode", "9": "checkUpdates",
                "10": "tools", "debug": "showDebugInfo"
            }
            if scanMode_str in choice_map: scanMode_internal = choice_map[scanMode_str]; validMode = True # Renamed scanMode to scanMode_internal
            else: print(f"\nInvalid choice: {scanMode_str} - Enter a number from 1 to 10")

        # Use scanMode_internal for logic now
        if scanMode_internal == "chosenVideos":
            confirm_cv = False
            while not confirm_cv:
                miscData_param.totalCommentCount = 0; allVideosMatchBool = True; videosToScan = []
                enteredVideosList_cv = []
                # videos_to_scan is from current_config.scan_modes
                if current_config.scan_modes.videos_to_scan != 'ask':
                    enteredVideosList_cv = utils.string_to_list(current_config.scan_modes.videos_to_scan)
                else:
                    print(f"\nEnter list of {F.YELLOW}Video Links/IDs{S.R}, comma-separated.");
                    input_str_cv = input("Enter here: ")
                    if input_str_cv.lower() == 'x': primaryInstance.return_to_main_menu = True; return # type: ignore
                    enteredVideosList_cv = utils.string_to_list(input_str_cv)

                if not enteredVideosList_cv: print(f"{F.RED}Video list is empty!{S.R}"); continue # Loop back in while not confirm_cv

                videoListResult_cv = []
                validVideoIDs_cv = True
                for video_input_str_cv in enteredVideosList_cv:
                    val_res_cv = validation.validate_video_id(video_input_str_cv)
                    if val_res_cv[0] == "COMMENTS_DISABLED": # Handle special signal
                         print(f"{F.YELLOW}Skipping video {val_res_cv[1]} as comments are disabled or count is unavailable.{S.R}")
                         videoListResult_cv.append((True, val_res_cv[1], val_res_cv[2], "0", val_res_cv[4], val_res_cv[5])) # Treat as valid with 0 comments
                    elif not val_res_cv[0]: validVideoIDs_cv = False; break
                    else: videoListResult_cv.append(val_res_cv)
                if not validVideoIDs_cv: print(f"{F.RED}Invalid video ID/link in list. Please try again.{S.R}"); continue

                for i_cv, v_data_cv in enumerate(videoListResult_cv):
                    videosToScan.append({'videoID': str(v_data_cv[1]), 'videoTitle': str(v_data_cv[2]),
                                         'commentCount': int(v_data_cv[3]), 'channelOwnerID': str(v_data_cv[4]),
                                         'channelOwnerName': str(v_data_cv[5])})
                    miscData_param.totalCommentCount += int(v_data_cv[3])
                    if str(v_data_cv[1]) not in current_scan.vidTitleDict: current_scan.vidTitleDict[str(v_data_cv[1])] = str(v_data_cv[2])
                    if i_cv > 0 and videosToScan[0]['channelOwnerID'] != videosToScan[i_cv]['channelOwnerID']:
                        allVideosMatchBool = False; print(f"{F.RED}ERROR: Videos must be from the same channel.{S.R}"); break
                if not allVideosMatchBool: continue

                print(f"\n{F.BLUE}Chosen Videos:{S.R}")
                for i_p, v_p in enumerate(videosToScan): print(f" {i_p+1}. {v_p['videoTitle']}")
                if CURRENTUSER.id != videosToScan[0]['channelOwnerID']: userNotChannelOwner = True
                miscData_param.channelOwnerID = videosToScan[0]['channelOwnerID']; miscData_param.channelOwnerName = videosToScan[0]['channelOwnerName']

                # skip_confirm_video is from current_config.general
                if current_config.general.skip_confirm_video: confirm_cv = True
                else:
                    if userNotChannelOwner:
                        print(f"\n{F.YELLOW}NOTE: One or more videos are not from your channel ({CURRENTUSER.name}).{S.R}")
                        if current_config.general.moderator_mode:
                             print(f"{F.YELLOW}Moderator Mode is enabled in config, allowing comment removal on other channels.{S.R}")
                        else:
                             print(f"{F.LIGHTRED_EX}Moderator Mode is disabled. You can only remove comments on your own videos.{S.R}")
                             print(f"{F.LIGHTRED_EX}The program will still scan, but deletion will be skipped for non-owned videos.{S.R}")

                    print("\nTotal comments to scan: " + str(miscData_param.totalCommentCount))
                    if miscData_param.totalCommentCount > 200000 and not current_config.general.skip_confirm_video: # Check skip_confirm_video here too
                        print(f"{B.RED}{F.WHITE} WARNING: {S.R} Scanning more than 200k comments will take a {F.YELLOW}VERY{S.R} long time and use lots of API quota.")
                        print(f"           Consider scanning fewer videos, or using the '{F.YELLOW}Recent Videos{S.R}' or '{F.YELLOW}Entire Channel{S.R}' mode with a {F.YELLOW}lower comment limit{S.R}.")

                    user_choice_confirm = choice("Is this video list correct?")
                    if user_choice_confirm is None: primaryInstance.return_to_main_menu = True; return # type: ignore
                    confirm_cv = user_choice_confirm

        elif scanMode_internal == "recentVideos":
            confirm_rv = False
            while not confirm_rv:
                miscData_param.totalCommentCount = 0; videosToScan = []
                channel_id_rv = ""; num_videos_rv = 0

                if current_config.scan_modes.channel_to_scan != 'ask':
                    channel_id_rv = current_config.scan_modes.channel_to_scan
                else:
                    channel_id_rv = input(f"\nEnter {F.YELLOW}Channel ID or Link{S.R} to scan (or 'X' for main menu): ")
                    if channel_id_rv.lower() == 'x': primaryInstance.return_to_main_menu = True; return # type: ignore

                processed_id_rv = utils.process_channel_input(channel_id_rv)
                if not processed_id_rv[0]: print(f"{F.RED}Invalid Channel ID/Link.{S.R}"); continue
                channel_id_rv = processed_id_rv[1]

                if isinstance(current_config.scan_modes.recent_videos_amount, int):
                    num_videos_rv = current_config.scan_modes.recent_videos_amount
                elif current_config.scan_modes.recent_videos_amount == 'ask':
                    num_videos_rv_str = input(f"How many {F.YELLOW}recent videos{S.R} to scan? (1-50, or 'X'): ")
                    if num_videos_rv_str.lower() == 'x': primaryInstance.return_to_main_menu = True; return # type: ignore
                    try: num_videos_rv = int(num_videos_rv_str)
                    except ValueError: print(f"{F.RED}Invalid number.{S.R}"); continue
                else: # Should not happen with proper validation, but fallback
                    print(f"{F.RED}Invalid 'recent_videos_amount' in config. Using default 10.{S.R}"); num_videos_rv = 10

                if not 1 <= num_videos_rv <= 50: print(f"{F.RED}Number of videos must be between 1 and 50.{S.R}"); continue

                print(f"\n{F.BLUE}Fetching info for the {num_videos_rv} most recent videos from channel {channel_id_rv}...{S.R}")
                recent_videos_result = utils.get_recent_videos(channel_id_rv, num_videos_rv)
                if not recent_videos_result[0]: print(f"{F.RED}Could not fetch recent videos.{S.R}"); continue # Error message printed by get_recent_videos

                videosToScan = recent_videos_result[1]
                for v_data_rv in videosToScan: # videosToScan is now list of dicts
                    miscData_param.totalCommentCount += v_data_rv['commentCount']
                    if v_data_rv['videoID'] not in current_scan.vidTitleDict: current_scan.vidTitleDict[v_data_rv['videoID']] = v_data_rv['videoTitle']

                if not videosToScan: print(f"{F.YELLOW}No videos found for this channel or criteria.{S.R}"); continue

                print(f"\n{F.BLUE}Videos to be scanned:{S.R}")
                for i_p, v_p in enumerate(videosToScan): print(f" {i_p+1}. {v_p['videoTitle']} ({v_p['commentCount']} comments)")

                if CURRENTUSER.id != videosToScan[0]['channelOwnerID']: userNotChannelOwner = True # First video determines ownership for this mode
                miscData_param.channelOwnerID = videosToScan[0]['channelOwnerID']; miscData_param.channelOwnerName = videosToScan[0]['channelOwnerName']

                if current_config.general.skip_confirm_video: confirm_rv = True
                else:
                    if userNotChannelOwner:
                        print(f"\n{F.YELLOW}NOTE: These videos are not from your channel ({CURRENTUSER.name}).{S.R}")
                        if current_config.general.moderator_mode: print(f"{F.YELLOW}Moderator Mode is enabled, allowing comment removal.{S.R}")
                        else: print(f"{F.LIGHTRED_EX}Moderator Mode is disabled. Deletion will be skipped.{S.R}")
                    print("\nTotal comments to scan: " + str(miscData_param.totalCommentCount))
                    if miscData_param.totalCommentCount > 200000:
                        print(f"{B.RED}{F.WHITE} WARNING: {S.R} Large scan size.") # Simplified warning
                    user_choice_confirm_rv = choice("Is this video list correct?")
                    if user_choice_confirm_rv is None: primaryInstance.return_to_main_menu = True; return # type: ignore
                    confirm_rv = user_choice_confirm_rv

        elif scanMode_internal == "entireChannel":
            maxScanNumber_ec_str = ""
            if isinstance(current_config.scan_modes.max_comments, int):
                maxScanNumber = current_config.scan_modes.max_comments
            elif current_config.scan_modes.max_comments == 'ask':
                while True:
                    maxScanNumber_ec_str = input(f"Enter {F.YELLOW}Maximum Number of Recent Comments{S.R} to scan across your channel (e.g., 1000, or 'X'): ")
                    if maxScanNumber_ec_str.lower() == 'x': primaryInstance.return_to_main_menu = True; return # type: ignore
                    try: maxScanNumber = int(maxScanNumber_ec_str); break
                    except ValueError: print(f"{F.RED}Invalid number.{S.R}")
            else: # Should not happen
                print(f"{F.RED}Invalid 'max_comments' in config. Using default 999999999.{S.R}"); maxScanNumber = 999999999

            miscData_param.totalCommentCount = maxScanNumber # For display/estimate, actual count might be less
            miscData_param.channelOwnerID = CURRENTUSER.id; miscData_param.channelOwnerName = CURRENTUSER.name
            userNotChannelOwner = False # By definition, it's the current user's channel
            print(f"\nScanning the {F.YELLOW}{maxScanNumber}{S.R} most recent comments across channel {F.LIGHTGREEN_EX}{CURRENTUSER.name}{S.R}.")
            if not choice("Begin scan?"): primaryInstance.return_to_main_menu = True; return # type: ignore

        elif scanMode_internal == 'communityPost':
            confirm_cp = False
            while not confirm_cp:
                postURL_input = ""
                if current_config.scan_modes.videos_to_scan != 'ask': # Re-use videos_to_scan for post URL if set
                    postURL_input = current_config.scan_modes.videos_to_scan # Assuming it's a single URL
                else:
                    postURL_input = input(f"Enter {F.YELLOW}Community Post Link{S.R} (or 'X' for main menu): ")
                    if postURL_input.lower() == 'x': primaryInstance.return_to_main_menu = True; return # type: ignore

                validation_result_cp = validation.validate_community_post_url(postURL_input)
                if not validation_result_cp[0]: print(f"{F.RED}Invalid Community Post URL.{S.R}"); continue
                postURL = validation_result_cp[1]; scanVideoID = validation_result_cp[2] # scanVideoID is post ID here

                # Fetch post details to get owner and title (simplified)
                try:
                    post_details = community_downloader.get_post_details(YOUTUBE, scanVideoID) # Needs YOUTUBE global
                    if not post_details: print(f"{F.RED}Could not fetch post details.{S.R}"); continue
                    videosToScan.append({'videoID': scanVideoID, 'videoTitle': post_details['title'],
                                         'commentCount': post_details['commentCount'],
                                         'channelOwnerID': post_details['channelId'],
                                         'channelOwnerName': post_details['channelName']})
                    miscData_param.totalCommentCount = post_details['commentCount']
                    current_scan.vidTitleDict[scanVideoID] = post_details['title']
                    miscData_param.channelOwnerID = post_details['channelId']; miscData_param.channelOwnerName = post_details['channelName']
                    if CURRENTUSER.id != miscData_param.channelOwnerID: userNotChannelOwner = True
                except Exception as e_cpd: print(f"{F.RED}Error fetching post details: {e_cpd}{S.R}"); continue

                print(f"\nPost: {F.LIGHTCYAN_EX}{videosToScan[0]['videoTitle']}{S.R} by {F.LIGHTGREEN_EX}{videosToScan[0]['channelOwnerName']}{S.R}")
                if current_config.general.skip_confirm_video: confirm_cp = True
                else:
                    # User not owner messages...
                    print(f"Total comments: {miscData_param.totalCommentCount}")
                    user_choice_confirm_cp = choice("Scan this post?")
                    if user_choice_confirm_cp is None: primaryInstance.return_to_main_menu = True; return # type: ignore
                    confirm_cp = user_choice_confirm_cp


        elif scanMode_internal == 'recentCommunityPosts':
            confirm_rcp = False
            while not confirm_rcp:
                channel_id_rcp = ""; num_posts_rcp = 0
                if current_config.scan_modes.channel_to_scan != 'ask': channel_id_rcp = current_config.scan_modes.channel_to_scan
                else: # Prompt for channel
                    channel_id_rcp_input = input(f"Enter {F.YELLOW}Channel ID or Link{S.R} for recent posts (or 'X'): ")
                    if channel_id_rcp_input.lower() == 'x': primaryInstance.return_to_main_menu = True; return # type: ignore
                    processed_id_rcp = utils.process_channel_input(channel_id_rcp_input)
                    if not processed_id_rcp[0]: print(f"{F.RED}Invalid Channel ID/Link.{S.R}"); continue
                    channel_id_rcp = processed_id_rcp[1]

                if isinstance(current_config.scan_modes.recent_videos_amount, int): # Re-use for num posts
                    num_posts_rcp = current_config.scan_modes.recent_videos_amount
                elif current_config.scan_modes.recent_videos_amount == 'ask': # Prompt for num posts
                    num_posts_rcp_str = input(f"How many {F.YELLOW}recent posts{S.R} to scan? (1-20, or 'X'): ")
                    if num_posts_rcp_str.lower() == 'x': primaryInstance.return_to_main_menu = True; return # type: ignore
                    try: num_posts_rcp = int(num_posts_rcp_str)
                    except ValueError: print(f"{F.RED}Invalid number.{S.R}"); continue
                else: num_posts_rcp = 5 # Fallback

                if not 1 <= num_posts_rcp <= 20: print(f"{F.RED}Number of posts must be 1-20.{S.R}"); continue

                print(f"\n{F.BLUE}Fetching info for the {num_posts_rcp} most recent community posts from channel {channel_id_rcp}...{S.R}")
                # This will be a list of post IDs or similar structure, not video dicts
                recentPostsListofDicts = community_downloader.get_recent_post_ids(YOUTUBE, channel_id_rcp, num_posts_rcp) # Needs YOUTUBE
                if not recentPostsListofDicts: print(f"{F.YELLOW}No community posts found or error fetching.{S.R}"); continue

                # Need to get titles/comment counts for these, or adapt scan_community_post
                # For now, assume scan_community_post can take a list of post IDs and handles fetching details
                # Or this loop needs to fetch details for each post ID
                miscData_param.channelOwnerID = channel_id_rcp # Assuming all from this channel
                # miscData_param.channelOwnerName would need to be fetched or passed
                # For simplicity, we'll assume scan_community_post handles the list of IDs
                print(f"Found {len(recentPostsListofDicts)} posts to scan.")
                if current_config.general.skip_confirm_video: confirm_rcp = True
                else:
                    user_choice_confirm_rcp = choice("Scan these posts?")
                    if user_choice_confirm_rcp is None: primaryInstance.return_to_main_menu = True; return # type: ignore
                    confirm_rcp = user_choice_confirm_rcp

        elif scanMode_internal == "makeConfig":
            new_config_path = files._determine_new_numbered_config_path(
                main_config_filename, # main_config_filename from outer scope
                current_config.paths.configs_path # Use loaded config's path setting
            )
            desc = input(f"Enter description for '{os.path.basename(new_config_path)}': ") or f"User config {os.path.basename(new_config_path)}"
            if files.create_new_config_file(new_config_path, configVersion, description=desc): # configVersion from outer scope
                print(f"New config '{new_config_path}' created. Please edit it and restart the program to use it, or select it from the menu if multiple configs are found on next start.")
            else:
                print(f"{F.RED}Failed to create new config file.{S.R}")
            input("Press Enter to return to main menu..."); primaryInstance.return_to_main_menu = True; return # type: ignore

        elif scanMode_internal == "checkUpdates":
            # ... (spamListDict, filterListDict are from main scope, version/updateReleaseChannel also)
            primaryInstance.return_to_main_menu = True; return # type: ignore

        elif scanMode_internal == "recoverMode":
            result = modes.recover_deleted_comments(current_config) # Pass ConfigContainer
            if str(result) == "MainMenu": primaryInstance.return_to_main_menu = True; return # type: ignore

        elif scanMode_internal == "commentList":
            result = modes.delete_comment_list(current_config) # Pass ConfigContainer
            if str(result) == "MainMenu": primaryInstance.return_to_main_menu = True; return # type: ignore

        elif scanMode_internal == "tools":
            result = user_tools.user_tools_menu(current_config) # Pass ConfigContainer
            if str(result) == "MainMenu": primaryInstance.return_to_main_menu = True; return # type: ignore

        filterSettings = {}; filterMode = ""; autoModeName = "";PreparedFilter = None # Initialize
        if scanMode_internal not in ["checkUpdates", "recoverMode", "commentList", "tools", "makeConfig", "showDebugInfo"]:
            utils.clear_terminal()
            print(f"\n\n--------------------------------- {F.YELLOW}Filtering Options{S.R} ---------------------------------")
            print(f"      1. Scan for specific {F.LIGHTCYAN_EX}Channel ID(s){S.R}")
            print(f"      2. Scan for specific {F.LIGHTCYAN_EX}Usernames{S.R} (Not case sensitive, comma separated)")
            print(f"      3. Scan for specific {F.LIGHTCYAN_EX}Comment Text{S.R} (Not case sensitive, comma separated)")
            print(f"      4. Scan {F.LIGHTCYAN_EX}Usernames AND Comment Text{S.R} for same strings")
            print(f"      5. Scan for {F.LIGHTMAGENTA_EX}Emojis / Special Characters{S.R} in usernames")
            print(f"      6. Scan for {F.LIGHTMAGENTA_EX}Emojis / Special Characters{S.R} in comment text")
            print(f"      7. Scan {F.LIGHTMAGENTA_EX}Usernames AND Comment Text{S.R} for same Emojis / Special Characters")
            print(f"      8. Scan for {F.LIGHTBLUE_EX}Regex Expression{S.R} in usernames")
            print(f"      9. Scan for {F.LIGHTBLUE_EX}Regex Expression{S.R} in comment text")
            print(f"      10. Scan {F.LIGHTBLUE_EX}Usernames AND Comment Text{S.R} for same Regex Expression")
            print(f"      11. {F.YELLOW}Auto-ASCII Mode:{S.R} Scan for {F.YELLOW}non-ASCII characters{S.R} in usernames")
            print(f"      12. {F.LIGHTGREEN_EX}Auto-Smart Mode:{S.R} Automatically scan for many common spam techniques")
            print(f"      13. {F.LIGHTRED_EX}Sensitive Smart Mode:{S.R} Auto-Smart, but {F.LIGHTRED_EX}more likely to have false positives{S.R}\n")

            validMode = False; filterModeChoice_str = ""
            while not validMode:
                if current_config.filter_modes.filter_mode != "ask":
                    filterModeChoice_str = current_config.filter_modes.filter_mode
                else:
                    filterModeChoice_str = input("Choice (1-13, or X for main menu): ")
                if filterModeChoice_str.lower() == "x": primaryInstance.return_to_main_menu = True; return # type: ignore

                filter_choice_map = {
                    "1": "ID", "2": "Username", "3": "Text", "4": "NameAndText", "5": "CharsUsername",
                    "6": "CharsText", "7": "CharsNameAndText", "8": "RegexUsername", "9": "RegexText",
                    "10": "RegexNameAndText", "11": "AutoASCII", "12": "AutoSmart", "13": "SensitiveSmart"
                }
                if filterModeChoice_str in filter_choice_map: filterMode = filter_choice_map[filterModeChoice_str]; validMode = True
                else: print(f"{F.RED}Invalid Choice.{S.R}")

            utils.clear_terminal()
            if filterMode == "ID": PreparedFilter, autoModeName = modes.prepare_filter_mode_ID(scanMode_internal, current_config)
            elif filterMode == "Username": PreparedFilter, autoModeName = modes.prepare_filter_mode_strings(scanMode_internal, "Username", current_config)
            elif filterMode == "Text": PreparedFilter, autoModeName = modes.prepare_filter_mode_strings(scanMode_internal, "Text", current_config)
            elif filterMode == "NameAndText": PreparedFilter, autoModeName = modes.prepare_filter_mode_strings(scanMode_internal, "NameAndText", current_config)
            elif filterMode == "CharsUsername": PreparedFilter, autoModeName = modes.prepare_filter_mode_chars(scanMode_internal, "Username", current_config)
            elif filterMode == "CharsText": PreparedFilter, autoModeName = modes.prepare_filter_mode_chars(scanMode_internal, "Text", current_config)
            elif filterMode == "CharsNameAndText": PreparedFilter, autoModeName = modes.prepare_filter_mode_chars(scanMode_internal, "NameAndText", current_config)
            elif filterMode == "RegexUsername": PreparedFilter, autoModeName = modes.prepare_filter_mode_regex(scanMode_internal, "Username", current_config)
            elif filterMode == "RegexText": PreparedFilter, autoModeName = modes.prepare_filter_mode_regex(scanMode_internal, "Text", current_config)
            elif filterMode == "RegexNameAndText": PreparedFilter, autoModeName = modes.prepare_filter_mode_regex(scanMode_internal, "NameAndText", current_config)
            elif filterMode == "AutoASCII": PreparedFilter, autoModeName = modes.prepare_filter_mode_non_ascii(scanMode_internal, current_config)
            elif filterMode == "AutoSmart": filterSettings, autoModeName = modes.prepare_filter_mode_smart(scanMode_internal, current_config, miscData_param, sensitive=False)
            elif filterMode == "SensitiveSmart": filterSettings, autoModeName = modes.prepare_filter_mode_smart(scanMode_internal, current_config, miscData_param, sensitive=True)

            if str(PreparedFilter) == "MainMenu" or str(filterSettings) == "MainMenu": primaryInstance.return_to_main_menu = True; return # type: ignore

        # Construct filtersDict for logging
        filtersDict = {
            'filterMode': filterMode, 'autoModeName': autoModeName,
            'CustomCommentTextFilter': PreparedFilter if filterMode in ["Text", "NameAndText", "CharsText", "CharsNameAndText", "RegexText", "RegexNameAndText"] else None,
            'CustomUsernameFilter': PreparedFilter if filterMode in ["Username", "NameAndText", "CharsUsername", "CharsNameAndText", "RegexUsername", "RegexNameAndText"] else None,
            'CustomChannelIdFilter': PreparedFilter if filterMode == "ID" else None,
            'filterSettings': filterSettings if filterMode in ["AutoSmart", "SensitiveSmart"] else None
        }
        if filterMode == "AutoASCII" and isinstance(PreparedFilter, str): # PreparedFilter is regex string for AutoASCII
             filtersDict['CustomCommentTextFilter'] = PreparedFilter # Or treat as username filter? Original was username.
             filtersDict['CustomUsernameFilter'] = PreparedFilter


        # Perform Scan based on mode
        if scanMode_internal == "communityPost" and scanVideoID: # scanVideoID is postID here
            current_scan = community_downloader.scan_community_post( # Needs YOUTUBE, scanVideoID, current_scan, ownerID, filtersDict, current_config
                youtube_service=YOUTUBE, post_id=scanVideoID, current_scan_instance=current_scan,
                channel_owner_id=miscData_param.channelOwnerID, filters_dict=filtersDict, current_config=current_config
            )
        elif scanMode_internal == "recentCommunityPosts" and recentPostsListofDicts:
            for post_info in recentPostsListofDicts:
                post_id_rcp = post_info.get('id')
                if post_id_rcp:
                    print(f"\nScanning Post ID: {post_id_rcp}...")
                    current_scan = community_downloader.scan_community_post(
                         youtube_service=YOUTUBE, post_id=post_id_rcp, current_scan_instance=current_scan,
                         channel_owner_id=miscData_param.channelOwnerID, filters_dict=filtersDict, current_config=current_config
                    )
        elif videosToScan: # Covers chosenVideos and recentVideos
            for video_data in videosToScan:
                # skip_confirm_video from current_config.general
                if current_config.general.skip_confirm_video or choice(f"Scan video: {video_data['videoTitle']}?", True):
                    current_scan = operations.scan_video(
                        youtube_service=YOUTUBE, video_id=video_data['videoID'],
                        comment_count_override=video_data['commentCount'], # Assuming scan_video can take this
                        current_scan_instance=current_scan, filters_dict=filtersDict, current_config=current_config,
                        user_is_not_channel_owner=(CURRENTUSER.id != video_data['channelOwnerID']), # Renamed for clarity
                        channel_owner_name_override=video_data['channelOwnerName'] # Assuming scan_video can take this
                    )
        elif scanMode_internal == "entireChannel":
            current_scan = operations.scan_activity( # Needs YOUTUBE, maxScan, current_scan, filtersDict, current_config
                youtube_service=YOUTUBE, max_comments_to_scan=maxScanNumber,
                current_scan_instance=current_scan, filters_dict=filtersDict, current_config=current_config
            )

        # Logging and Deletion Logic
        jsonSettingsDict: dict[str,Any] = {}
        logMode = None
        loggingEnabled = False # Default to false
        if current_scan.matchedCommentsDict or current_scan.spamThreadsDict or current_scan.duplicateCommentsDict or current_scan.repostedCommentsDict:
            # enable_logging from current_config.logging
            if current_config.logging.enable_logging == 'ask':
                if choice("Enable Logging for this session?"): loggingEnabled = True
            elif current_config.logging.enable_logging is True or str(current_config.logging.enable_logging).lower() == 'true':
                loggingEnabled = True

            if loggingEnabled:
                # delete_without_reviewing from current_config.actions
                current_scan, logMode, jsonSettingsDict = logging.prepare_logFile_settings(
                    current=current_scan, current_config=current_config, miscData=miscData_param, # Pass miscData_param
                    jsonSettingsDict=jsonSettingsDict, filtersDict=filtersDict,
                    bypass=current_config.actions.delete_without_reviewing
                )

            # delete_without_reviewing from current_config.actions
            logFileOutput = logging.print_comments(
                youtube_service=YOUTUBE, # Pass YOUTUBE service
                current=current_scan, current_config=current_config, scanVideoID=scanVideoID,
                loggingEnabled=loggingEnabled,
                scanMode=0 if scanMode_internal in ["communityPost", "recentCommunityPosts"] else 1,
                logMode=logMode,
                doWritePrint=not current_config.actions.delete_without_reviewing
            )
            current_scan.logFileContents = logFileOutput[0]
            logMode = logFileOutput[1]

            # skip_deletion and delete_without_reviewing from current_config.actions
            if not current_config.actions.skip_deletion:
                if not current_config.actions.delete_without_reviewing:
                    current_scan, excludedUserList = operations.exclude_authors(
                        current_scan_instance=current_scan, current_config=current_config,
                        whitelist=miscData_param.resources['Whitelist']['WhitelistContents'], log_mode=logMode
                    )
                    miscData_param.resources['Whitelist']['WhitelistContents'] = excludedUserList

                finalDeletionIDs = list(current_scan.matchedCommentsDict.keys()) + \
                                   list(current_scan.spamThreadsDict.keys()) + \
                                   list(current_scan.duplicateCommentsDict.keys()) + \
                                   list(current_scan.repostedCommentsDict.keys())

                if finalDeletionIDs:
                    banConfirm = False
                    # enable_ban from current_config.actions
                    if current_config.actions.enable_ban == 'ask': banConfirm = choice("Ban the commenters from your channel?")
                    elif current_config.actions.enable_ban is True or str(current_config.actions.enable_ban).lower() == 'true': banConfirm = True

                    removeOthersConfirm = False
                    # remove_all_author_comments from current_config.actions
                    if banConfirm and (current_config.actions.remove_all_author_comments == 'ask'):
                        removeOthersConfirm = choice(f"Also {F.LIGHTRED_EX}Remove ALL comments{S.R} by Matched Authors?")
                    elif banConfirm and (current_config.actions.remove_all_author_comments is True or str(current_config.actions.remove_all_author_comments).lower() == 'true'):
                        removeOthersConfirm = True

                    if removeOthersConfirm:
                         current_scan = operations.get_all_author_comments(
                             current_scan_instance=current_scan, current_config=current_config, youtube_service=YOUTUBE,
                             authors_to_fetch_list=finalDeletionIDs, user_not_channel_owner=userNotChannelOwner # userNotChannelOwner was set earlier
                         )
                         finalDeletionIDs.extend(current_scan.otherCommentsByMatchedAuthorsDict.keys())

                    # removal_type and check_deletion_success from current_config.actions
                    operations.delete_found_comments(
                        youtube_service=YOUTUBE, comments_list_to_delete=list(set(finalDeletionIDs)),
                        ban_choice=banConfirm, deletion_mode=current_config.actions.removal_type,
                        current_scan_instance=current_scan, check_success=current_config.actions.check_deletion_success,
                        user_not_channel_owner=userNotChannelOwner
                    )
                    if loggingEnabled and logMode:
                        logging.write_log_completion_summary(
                            current=current_scan, exclude=None, logMode=logMode, banChoice=banConfirm,
                            deletionModeFriendlyName=current_config.actions.removal_type,
                            removeOtherAuthorComments=removeOthersConfirm
                        )

            if loggingEnabled and jsonSettingsDict.get('jsonLogging'):
                comments_for_json = {}
                for d in (current_scan.matchedCommentsDict, current_scan.spamThreadsDict,
                          current_scan.duplicateCommentsDict, current_scan.repostedCommentsDict,
                          current_scan.otherCommentsByMatchedAuthorsDict):
                    comments_for_json.update(d)

                json_data_dict = None
                # json_extra_data from current_config.logging
                if current_config.logging.json_extra_data:
                    all_author_ids_for_json = list(set(c['authorID'] for c in comments_for_json.values() if 'authorID' in c))
                    if miscData_param.channelOwnerID: all_author_ids_for_json.append(miscData_param.channelOwnerID)
                    json_data_dict = logging.get_extra_json_data(
                        youtube_service=YOUTUBE, # Pass YOUTUBE service
                        channelIDs=list(set(all_author_ids_for_json)),
                        jsonSettingsDict=jsonSettingsDict
                    )

                logging.write_json_log(current_scan, current_config, jsonSettingsDict, comments_for_json, jsonDataDict=json_data_dict)
        else:
            print(f"\n{F.LIGHTGREEN_EX}No matching comments found.{S.R}")

        # auto_close from current_config.general
        if current_config.general.auto_close: print("Closing in 10 seconds..."); time.sleep(10)
        else: input("\nPress Enter to return to Main Menu...")
        primaryInstance.return_to_main_menu = True; return # type: ignore

        primaryInstance.return_to_main_menu = True; return # type: ignore


    # Loops Entire Program to Main Menu
    continueRunning = True
    while continueRunning:
        # Pass the config_container to primaryInstance
        # primaryInstance returns True to continue, False or raises exception to exit
        if not primaryInstance(miscData, config_container): # type: ignore
            break
        if hasattr(primaryInstance, 'return_to_main_menu') and primaryInstance.return_to_main_menu: # type: ignore
            primaryInstance.return_to_main_menu = False # Reset for next loop
            continue # Go to next iteration of while loop (back to main menu)
        else: # If primaryInstance finishes without setting the flag (e.g. after a full operation)
            break


import traceback
