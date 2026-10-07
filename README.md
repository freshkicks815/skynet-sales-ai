# Skynet Sales AI v0.6 — Brand Intelligence

This release adds a dedicated social-presence and brand-intelligence workflow.

## New

- Scans the official website for linked Facebook, Instagram, TikTok, YouTube, LinkedIn, X, and Pinterest profiles.
- Performs a best-effort public search for possible social profiles not linked from the website.
- Clearly separates **Confirmed**, **Possible**, and **Not found** results.
- Gives TikTok dedicated status and creative recommendations.
- Lets you manually verify and save official profile URLs.
- Saves confirmed profiles in SQLite and includes them in future website concepts.
- Generates a digital-presence score and brand-aware website recommendations.

## Install

1. Extract this ZIP into a new folder.
2. Copy your working `settings.json` from v0.4.1 into this folder.
3. Double-click `start_windows.bat`.
4. Open a lead and click **Scan Social Presence**.
5. Review possible matches and save only profiles you confirm belong to the business.

## Important

Public search results are candidates, not proof of ownership. Confirm each account before using its content. Do not download or republish social-media images or videos without the client's permission and any required platform authorization.


## v0.6 Sales Intelligence
- Admin pricing manager for Basic, E-Comm 1, and E-Comm 2.
- Monthly and yearly prices flow into smart recommendations and proposals.
- Opportunity scoring dashboard for each lead.
- Editable AI recommendation confidence, reasoning, plan, and billing option.
- Smart proposal with all plan choices and the recommended option highlighted.

Open **Plans** in the top navigation to edit pricing. Open any lead and choose **Sales Intelligence** to score and recommend a plan.

## v0.6.2 lead-search reliability fix

The Lead Finder now clears the previous query before each search, uses the current city/category on the first click, prevents overlapping submissions, and disables stale-page caching. The page also displays a **Current Search** summary above the results.
