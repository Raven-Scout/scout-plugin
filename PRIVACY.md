# Privacy Policy

**Last updated: June 23, 2026**

This Privacy Policy explains how the **Scout** project ("Scout", "we", "us", or "the
project" — the maintainers who publish the [Raven Scout](https://github.com/Raven-Scout)
repositories) handles information. It covers the Scout Claude Code plugin and the companion
apps for macOS, iOS, and Android, as well as this project's website.

## The short version

**Scout is local-first, and we collect nothing.** There is no Scout account, no Scout
server, no analytics, and no telemetry. The plugin runs as scheduled
[Claude Code](https://claude.com/claude-code) sessions on *your own* computer, and everything
it produces — your knowledge base, action items, logs, and configuration — is written to
files on *your* machine (by default, the `~/Scout/` folder) and to *your* own git history.
Your data never reaches us, because there is nowhere for it to go.

Because we never receive your data, there is nothing for us to sell, share, mine, or lose.
The honest privacy story for Scout is one of **architecture, not promises**: the data stays
where you are.

## Who is responsible

Scout is an independent, unincorporated open-source project maintained by the Raven Scout
contributors. We are not a registered company and we do not operate any hosted service.
For data-protection purposes, **you are the controller of your own data** — Scout is a tool
you run on your own infrastructure, under your own accounts. We are neither a controller nor
a processor of that data, because it is never transmitted to us.

## What each part of Scout does with data

### The Scout plugin (the engine)

The plugin runs inside Claude Code on your machine. It reads from the tools *you* connect —
for example Slack, Gmail, Google Calendar, Linear, GitHub, and meeting transcripts — using
*your own* credentials and connector authorizations, and it writes the results to local files
in your Scout vault (`~/Scout/` by default).

To do its work, the plugin sends relevant content to **Claude (Anthropic)** for processing —
this is inherent to how Claude Code works, and it happens under *your own* Claude Code / API
configuration, governed by Anthropic's terms and privacy policy, not ours. It also reads from,
and may write back to, the third-party tools you connect, each governed by that provider's own
terms. **We do not see, intercept, or receive any of this data.** See
["The data Scout reads from your tools"](#the-data-scout-reads-from-your-tools) below.

### Scout for macOS

The macOS app is a native interface over the files the plugin produces. It reads and writes
the `~/Scout/` folder on your Mac and runs local commands (`scoutctl`, `launchctl`, `git`) to
manage your schedule and action items. It does not contain analytics or crash reporting, does
not create any account, and does not send your data to us or to any third party.

### Scout for iOS

The iOS app reads a Scout vault folder that you select on your device — in practice, the vault
inside your own iCloud Drive / Obsidian folder — using a security-scoped bookmark that stays on
your device. Edits you make (marking items done, comments) are written back to the markdown
files and sync between *your* devices via *your* iCloud, which is governed by Apple's terms.
Notifications are generated locally on the device. The app has no Scout account and sends no
data to us.

### Scout for Android

The Android app reads your Scout action items and can capture device notifications to feed your
phone's activity back into Scout. Specifically:

- **Notification capture** uses Android's `NotificationListenerService`. When you enable it,
  the app stores captured notifications in a **local database on your phone** and forwards them
  over your **local network** to a bridge running on *your own* Mac, authenticated with a
  pairing secret. From there they are written into your Scout vault. This data goes **only to
  your own paired computer** — never to us or to any third-party server. Because notification
  content can be sensitive, this capture is something you turn on deliberately, and you can turn
  it off at any time in your system settings.
- **The app does not read SMS messages or health data.** Those capabilities are not part of the
  current version.
- **Push notifications (FCM) are dormant** and disabled unless you supply your own Firebase
  configuration. In the default build, no push token is registered with Google.
- The app requests network access solely to reach the bridge on your local network.

### The data Scout reads from your tools

This is the most important thing to understand: **Scout's entire purpose is to read your work
data and reason over it.** When it runs, it sends relevant content from the tools you connect to
**Claude (Anthropic)** so the model can cross-check and summarize it, and it reads from (and may
update) those connected tools. All of this happens:

- on your machine,
- under *your own* accounts, API keys, and connector authorizations, and
- subject to the privacy policies of **Anthropic** and of each tool you connect (e.g. Slack,
  Google, GitHub, Linear).

We are not a party to any of it. To understand how your data is handled there, see
[Anthropic's Privacy Policy](https://www.anthropic.com/legal/privacy) and the privacy policy of
each service you connect.

## This website

Our website (`raven-scout.github.io`) is a static informational site hosted on **GitHub Pages**.
It does **not** use cookies, analytics, tracking pixels, advertising, or third-party fonts, and
it makes no third-party network requests — fonts and styles are served from the site itself.

As with any website, the host (GitHub) may automatically log technical request data such as IP
addresses to operate and secure the service. That processing is performed by GitHub under
[GitHub's Privacy Statement](https://docs.github.com/site-policy/privacy-policies/github-privacy-statement),
not by us, and we do not access or retain it.

## Your rights (GDPR, UK GDPR, CCPA/CPRA, and similar laws)

Privacy laws such as the EU/UK GDPR and the California Consumer Privacy Act give you rights over
your personal data — including the rights to access, correct, delete, port, and object to the
processing of it.

Because **we do not collect or hold any of your personal data**, there is nothing in our
possession to access, correct, export, or delete, and:

- **We do not sell or share your personal information**, and we never have.
- We do not use your data for advertising or profiling.
- We do not transfer your data internationally, because we do not receive it.

The data Scout works with lives in *your* files, *your* accounts, and *your* connected services.
You exercise your rights there directly — by editing or deleting your local files and git
history, and by using the privacy controls of Anthropic and of each tool you connect. If you
have a question about this policy, you can [contact us](#contact).

## Children's privacy

Scout is a productivity tool intended for adults in a work context. It is not directed to
children, and we do not knowingly collect personal data from anyone, including children under 16
(or under 13 where that is the applicable threshold).

## Changes to this policy

If Scout's architecture ever changes in a way that affects privacy — for example, if a future
hosted or sync service is introduced — we will update this policy and revise the "Last updated"
date above before that change takes effect. Because every change is tracked in git, you can see
the full history of this document in the repository.

## Contact

Scout has no hosted support desk. For privacy questions or requests, open an issue at
[github.com/Raven-Scout/scout-plugin/issues](https://github.com/Raven-Scout/scout-plugin/issues),
or, for sensitive or security-related matters, use
[GitHub Security Advisories](https://github.com/Raven-Scout/scout-plugin/security/advisories) as
described in our [Security Policy](https://github.com/Raven-Scout/.github/blob/main/SECURITY.md).
