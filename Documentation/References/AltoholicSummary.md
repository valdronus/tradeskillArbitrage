# Altoholic Addon Summary

`Altoholic` is a World of Warcraft addon that collects and displays cross-character data for alts, including inventory, bank, mail, professions, reputations, guild information, and more. It integrates with the DataStore ecosystem to read shared character data and extend tooltips and search features with item counts across your whole account and guild.

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

## Communication and sharing

- `Comm.lua`
  - Implements account sharing protocol using AceComm.
  - Handles request/accept/refuse flows and transfer acknowledgments.
  - Supports remote transfer of DataStore content, bank tabs, and reference data.
  - Includes logic to import shared character data and finalize imported DataStore entries.

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

## Task scheduling

- `Tasks.lua`
  - Simple task manager used by the addon for delayed operations.
  - Stores tasks in a list and executes them via an OnUpdate timer.
  - Supports adding, removing, rescheduling, and retrieving tasks.

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

## High-level responsibilities

`Altoholic` is designed to gather and present cross-character information from DataStore for WoW.

Key responsibilities include:
- aggregating inventory and bag data
- showing bank and guild bank holdings
- surfacing mail and auction stats
- displaying professions, recipes, talents, and reputations
- providing tooltip counters and item search across alts
- supporting account sharing and remote data imports

## Notes

- This addon targets Wrath-era WoW with interface version `30300`.
- The main logic is split across `Altoholic.lua` (UI utilities and events) and `Core.lua` (bootstrap and defaults).
- Feature-specific UI/logic modules are organized under `Frames/`.
