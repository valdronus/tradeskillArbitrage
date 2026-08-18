# Altoholic Addon Summary

`Altoholic` is a World of Warcraft addon that collects and displays cross-character data for alts, including inventory, bank, mail, professions, reputations, guild information, and more. It integrates with the DataStore ecosystem to read shared character data and extend tooltips and search features with item counts across your whole account and guild.

## Table of Contents

- [Entry point](#entry-point)
- [Core bootstrap and add-on lifecycle](#core-bootstrap-and-add-on-lifecycle)
- [Communication and sharing](#communication-and-sharing)
- [Tooltip enhancement](#tooltip-enhancement)
- [Task scheduling](#task-scheduling)
- [UI structure and modules](#ui-structure-and-modules)
- [Summary and tab management](#summary-and-tab-management)
- [High-level responsibilities](#high-level-responsibilities)
- [Saved variables and persistence](#saved-variables-and-persistence)
- [Notes](#notes)

The addon works by registering game events and DataStore messages, then building UI views from saved global data. It initializes with Ace3 libraries, loads XML UI frames, and attaches handlers to update its views when inventory, guild, or tooltip information changes.

This document summarizes the major parts of the `Altoholic` World of Warcraft addon from the cloned repository in `Documentation/References/Altoholic`.

## Entry point

- `Altoholic.lua`
  - Main add-on utility and event wiring.
  - Defines localization initialization and common color constants.
  - Implements shared data access helpers:
    - `GetCharacterTable`
    - `GetGuild`
    - `GetCurrentCharacter`
    - `SetCurrentCharacter`
  - Provides UI and item utility functions:
    - `Item_OnEnter`
    - `Item_OnClick`
    - `SetItemButtonTexture`
    - `GetMoneyString`
  - Includes scanning logic for:
    - friend list updates
    - saved raid instances
    - loot tracking for timed items
  - Contains `addon:OnEnable()` to initialize the addon and register key game events.
  - This file acts as a central utility layer, supplying helpers and event hooks that many other modules rely on when they need current character context, display formatting, or game-state information.

## Core bootstrap and add-on lifecycle

- `Core.lua`
  - Creates the AceAddon object and registers core Ace3 modules:
    - `AceConsole-3.0`
    - `AceEvent-3.0`
    - `AceComm-3.0`
    - `AceSerializer-3.0`
  - Sets addon version metadata and launcher object.
  - Defines default saved-variable structure in `AddonDB_Defaults`.
  - Controls initialization and enable phases:
    - `OnInitialize()` sets up the database, options, comm callbacks, and DataStore message handlers.
    - `OnEnable()` initializes options, tasks, profiler, tooltip system, frames, and event handlers.
  - Adds chat command support for `/Altoholic` and `/Alto`.
  - This is the startup backbone of the addon. It creates the shared environment and makes sure the rest of the modules are loaded and connected to the right events and saved data.
  - `OnEnable()` also resolves the current character key and assigns `addon.ThisCharacter` from `addon.db.global.Characters[Account.Realm.Name]`, which is how the addon keeps per-character metadata available for the current session.

## Communication and sharing

- `Comm.lua`
  - Implements account sharing protocol using AceComm.
  - Handles request/accept/refuse flows and transfer acknowledgments.
  - Supports remote transfer of DataStore content, bank tabs, and reference data.
  - Includes logic to import shared character data and finalize imported DataStore entries.
  - This subsystem extends the addon beyond local data by allowing one player or account to share information with another, turning the UI into a broader account-wide or cross-player view once imported.

## Tooltip enhancement

- `Tooltip.lua`
  - Adds item count and source information to tooltips.
  - Computes item totals across characters, accounts, and guild banks.
  - Supports gathering node item count display for herb and mining nodes.
  - Integrates with DataStore to retrieve item counts from:
    - bags
    - bank
    - auction house
    - equipped items
    - mail
    - currency holdings
  - This is one of the most user-facing features because it makes Altoholic feel integrated into normal gameplay, showing account-wide context directly while hovering over items or gathering nodes.

## Task scheduling

- `Tasks.lua`
  - Simple task manager used by the addon for delayed operations.
  - Stores tasks in a list and executes them via an OnUpdate timer.
  - Supports adding, removing, rescheduling, and retrieving tasks.
  - Although lightweight, this module helps keep the UI and other systems responsive by deferring work that should happen after the current event cycle or after a short delay.

## UI structure and modules

- `Altoholic.toc`
  - Defines addon metadata, dependencies, and loaded files.
  - Loads XML UI definitions, embedded libraries, and localization files.
  - Loads core frame modules and feature pages.
  - Main UI tabs include:
    - `Summary`
    - `Characters`
    - `Search`
    - `GuildBank`
    - `Achievements` (load-on-demand module)
  - Various feature modules under `Frames/` correspond to UI pages and data views.
  - This file is effectively the load-order blueprint for the addon, ensuring that the core systems, UI frames, and feature modules are available in the right sequence.

## Summary and tab management

- `Frames/TabSummary.lua`
  - Manages the Summary tab and its child views.
  - Supports multiple summary modes:
    - Account Summary
    - Bag Usage
    - Professions and Skills
    - Activity (mail/auctions)
    - Guild Members
    - Guild Professions
    - Guild Bank Tabs
    - Calendar
  - Builds dynamic sortable column headers.
  - Coordinates refresh and view updates for the visible summary page.
  - This is a good example of how the UI modules work in tandem with the rest of the addon: they take the shared data already prepared by the core systems and present it through a flexible, switchable interface.

## High-level responsibilities

`Altoholic` is designed to gather and present cross-character information from DataStore for WoW.

Key responsibilities include:
- aggregating inventory and bag data from saved variables
- showing bank and guild bank holdings from saved character/guild data
- surfacing mail and auction stats
- displaying professions, recipes, talents, and reputations
- providing tooltip counters and item search across alts and guild banks
- supporting account sharing and remote data imports

Taken together, these responsibilities show that the addon is less about a single feature and more about building one coherent picture of a player’s alts, guild, and account data from many different sources.

## Saved variables and persistence

The addon stores its configuration and lightweight metadata in AceDB saved variables under `AltoholicDB`, while the bulk of inventory-related content such as bags, bank contents, and guild bank tabs is persisted in DataStore-backed saved variables. Character entries are keyed by `Account.Realm.Character` and include state for friends lists, raid timers, calendar events, profession cooldowns, and related per-character metadata.

The actual item caches are kept in separate module saved variables: `DataStore_Containers` for bags/bank/guild bank, `DataStore_Inventory` for equipped gear, `DataStore_Mails` for mailbox contents, and other `DataStore_*` files for professions, quests, reputations, pets, etc. This split-module design keeps the database manageable and lets Altoholic reference the latest inventory data through the DataStore API.

`Altoholic` stores its UI settings, metadata, account-sharing flags, and light per-character state in `AltoholicDB` using AceDB. The heavier item and inventory content is stored in separate DataStore saved-variable modules, which keeps the overall database split and manageable.

- Per-character metadata in `AltoholicDB` is indexed by the composite key `Account.Realm.Character`.
- Detailed inventory and container state is held in DataStore modules such as `DataStore_Containers` for bags, bank, and guild bank tabs, `DataStore_Inventory` for equipped gear, and `DataStore_Mails` for mailbox contents.
- Other DataStore modules cover professions, quests, reputations, pets, talents, and more.
- Guild storage is grouped under account/realm/guild, with guild bank tabs stored per-tab and only populated after that tab has been opened in-game.
- The WoW client writes saved variables to disk at logout or UI reload, while DataStore modules keep their tables updated live during the session through event-driven scans.
- Bag and bank contents are refreshed by opening the corresponding frames; guild bank tab contents are read tab by tab; mailbox contents require a visit to a mailbox; professions require opening the tradeskill pane.
- DataStore maintains freshness metadata via module timestamps and last-update values, which Altoholic uses when importing, sharing, or deciding whether a dataset is current.

Key code references:
- `Documentation/References/Altoholic/Altoholic.lua` lines 141-147: `addon:GetCharacterTable()` resolves the `Account.Realm.Character` key in `addon.db.global.Characters`.
- `Documentation/References/Altoholic/Altoholic.lua` lines 314-330: `addon:OnEnable()` and event registration for `PLAYER_LOGOUT`, `UPDATE_INSTANCE_INFO`, and other inventory-related updates.
- `Documentation/References/Altoholic/Comm.lua` lines 405-413: DataStore sharing logic uses `DS:GetCharacterTable()` to ship `DataStore_Characters`, `DataStore_Stats`, and optional DataStore module tables.
- `Documentation/References/Altoholic/Frames/AccountSharing.lua` lines 500-505 and 511-529: shared guild bank tab serialization and module last-update timestamp tracking for import/sharing.
- `Documentation/References/Altoholic/Tooltip.lua` lines 242-260: guild bank tab and guild bank item count retrieval for tooltip display.

## Notes

- This addon targets Wrath-era WoW with interface version `30300`.
- The main logic is split across `Altoholic.lua` (UI utilities and events) and `Core.lua` (bootstrap and defaults).
- Feature-specific UI/logic modules are organized under `Frames/`.
