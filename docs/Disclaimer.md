# F3 Password Vault: Disclaimer for Regions

F3 Nation IT runs a shared password vault (powered by **Vaultwarden**, an open-source version of Bitwarden) so **Regions** can safely store and hand off their digital accounts.

This is not a legal agreement. It just explains how the vault works and what you need to do.

---

## The Short Version

1. The Nation hosts this vault and offers it free for regions to use.
2. The vault is set up so passwords aren't lost when region leaders change. The implication of that is that the Nation can access anything you store here.
3. Nothing technically stops Nation admins or Area/Territory/Sector admins from seeing region passwords.
4. All Nation admins promise not to use region passwords.
5. On initial login, you'll set a **master password**. Keep it private. Others in your region don't need it to get the credentials you share. They will have their own account with their own master password if they need access.
6. One pax can access multiple regions' passwords if they're given permission.

---

## Why the Nation Provides This

In F3, leadership rotates constantly. Technology like domains, email, social accounts, and Slack primary owner logins often get passed around over text, personal email, or sticky notes. That causes problems:

* **Lost access:** A pax rolls off or goes dark, and the region can't get in.
* **Security risk:** Passwords sent over Slack or SMS can be stolen.
* **Painful handoffs:** New pax waste weeks hunting down logins.

With a central vault, the Nation handles the server, backups, and security. Each region gets a secure space that belongs to the region, not to one guy's inbox.

---

## How It Works

* **Region Collections:** Each region gets its own **Collection** in the vault.
* **Guaranteed access:** Personal storage is turned off. Every password must go in a Collection, so the region keeps access even if a pax leaves suddenly.
* **Easy handoffs:** A Regional Admin grants access by adding the `password_manager` or `password_reader` role in the F3 Nation Admin interface, and removes it when a pax rolls off.

---

## Security

Your credentials are encrypted on your device before they reach the server, and stay encrypted in transit and at rest. This is called **zero-knowledge encryption**.

---

## Your Responsibilities

### 1. Use a strong master password

If someone guesses your master password, they can see all your region's credentials.

### 2. Write it down

There is **no "Forgot Password" link.**

* If you forget it, your account must be deleted and recreated.
* No stored passwords are lost when an individual account is deleted.
* Keep your master password somewhere safe.

### 3. Everything goes in a Collection

When you save a credential, you'll be prompted to pick your region's Collection.

### 4. Keep personal out

Only store **F3 accounts** here, never personal banking, email, or household passwords.

### 5. Be careful with extensions and mobile apps

For accessing region passwords, we prefer you log into [passwords.f3nation.com](https://passwords.f3nation.com) in your browser. Because this is built around Bitwarden, you can use the Bitwarden mobile app or browser extension to log into the F3 password vault. But when you do this, your phone and your browser will likely prompt you to save credentials for non-F3 logins in the F3 fault and thereby exposing your accounts. This cross contamination is less likely to happen if you just log into the website to retrieve passwords.

---

Thanks for stepping up and keeping our regional tech secure
