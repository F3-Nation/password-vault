# F3 Password Vault: Disclaimer for Regions

Welcome to the **F3 Nation Password Vault**! Managed centrally by **F3 Nation IT**, this secure vault powered by **Vaultwarden** (an open-source implementation of Bitwarden) is provided to help **Regions** safely store, share, and pass down digital assets.

This guide explains **why** Nation provides this resource, **how** regional Collections work, and **your responsibilities** as a PAX or Q managing regional tech.

---

## TLDR

1. The Nation is hosting an instance of Bitwarden and offering it as a free service for regions to use.
1. In order to ensure that passwords are not lost when region leadership changes hands, the password vault is structured in a way that the Nation will have access to any passwords you store in it.
1. There is no technical mechanism to prevent Nation admins from seeing passwords stored by Regions.
1. All Nation admins commit to not using Region passwords.
1. Everyone to access the password vault will need to set up a master password. This should not be shared. Others in your region don't need your master password to access credentials.
1. A single pax can access passwords from multiple regions if they have the necessary permissions.

---

## Why F3 Nation Provides This

In F3, **leadership is fluid**. Roles like Nant'an, Weaselshaker, ITQ, Commz Q, and Site Qs constantly rotate as men step up or roll off.

Historically, regional tech—such as website domains, email, social media accounts, and Slack workspace owner credentials—has been passed around via text messages, personal emails, physical notes, or region-owned password vault accounts. This creates serious risks for the region:

* **Orphaned Accounts:** A pax set up an account then moves or rolls off or goes dark, and the region loses access.
* **Security Exposure:** Sending passwords over unencrypted channels (Slack/SMS) leaves regional tools vulnerable to account takeovers.
* **Handoff Friction:** New pax waste weeks trying to track down credentials instead of focusing on their role.

By hosting this centrally, **F3 Nation handles the server, backups, and security infrastructure**, while giving each Region a secure container that belongs permanently to the region—not to any single guy's personal email.

---

## How It Works: Infrastructure & Regional Collections

F3 Nation owns the vault infrastructure and manages top-level access via Organization settings and emergency keys:

* **Regional Collections:** F3 Nation provisions a dedicated **Collection** for your Region.
* **Guaranteed Access:** Personal vault storage is disabled system-wide. Every password created in this vault **must** be assigned to a Collection. This ensures that even if a pax rolls off unexpectedly or leaves without sharing credentials, the Region maintain uninterrupted access to regional assets.
* **Easy Leadership Handoffs:** When a man steps up in your region, a Regional Admin simply assigns his account to that role's Collection. When he rolls off, access is removed in one click.

---

## Zero-Knowledge Security Model

Our vault uses **zero-knowledge encryption**:

1. **Client-Side Encryption:** Your credentials are encrypted locally on your device before they ever hit the server.
2. **Privacy & Security:** Data is encrypted in transit and at rest, protecting our regional credentials from external leaks or breaches.

---

## Critical Rules & Your Responsibilities

While Nation secures the infrastructure and guarantees data retention, local security depends on your individual habits:

### 1. Write Down Your Master Password

Because of zero-knowledge encryption, **there is NO "Forgot Password" link.**

* If you forget your Master Password, your account will have to be deleted and recreated. No passwords will be lost.
* **Action Required:** Write your Master Password down on paper when you set up your account and keep it somewhere safe at home.

### 2. All Items Belong to Collections

Because personal vaults are removed, any new credential you save will automatically prompt you to select your Region's Collection.

### 3. Keep Personal & F3 Separate

Do not attempt to store personal banking, personal emails, or private household passwords in this vault. It is strictly dedicated to **F3 operations**.

---

## Onboarding Steps for Regional Pax

1. **Receive Invite:** Look for an official email invitation sent by F3 Nation.
1. **Create Account:** Click the link, set up your account, and create a strong Master Password (write it down!).
1. **Get Access:** An admin will confirm your account and grant you access to your region's Collections.

*Aye!* Thank you for stepping up to lead and keeping our regional tech secure.