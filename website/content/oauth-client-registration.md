+++
title = "Register the distributed Gmail client"
description = "The one-time Google OAuth setup performed by an Email Collection Toolkit release maintainer."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->


This procedure supports the experimental future Gmail API adapter. Google
Takeout is the supported end-user path today; see [Archive Gmail](../gmail-authorization/).

> **Maintainers only. End users do not perform these steps.** An Email Collection Toolkit
> release carries one Desktop client registered by the project maintainer.

> **Use your own Gmail or Google Workspace address everywhere.** The diagrams
> below are schematic setup illustrations, not Google Console screenshots.
> Example fields use the current project name and a placeholder email address.

The Cloud project and Desktop client are registered once. They do **not** expire
after seven days. In Testing, each test user's authorization expires after seven
days; that user authorizes again without the maintainer recreating the project
or client. Google may change the labels, arrangement, or requirements.

## Start the one-time registration

Replace the example with the Google account that will own the project:

```console
uv run mailarchiver-auth --register-client your.name@gmail.com
```

Confirm project creation when prompted. The program creates a personal Cloud
project, enables the Gmail API, and opens the project-specific Google Auth
pages. It never asks for your Google password.

## Configure Google Auth

1. On **Branding**, select **Get started**.

![Google Auth Platform Branding page with the Get started button](../images/gmail-authorization/01-get-started.png)

2. Enter `Email Collection Toolkit personal` as the app name. For **User support email**,
   select **your own Google address**, then select **Next**.

![App Information with the app name and support-email selector](../images/gmail-authorization/02-app-information.png)

3. For a personal Gmail account, choose **External**, then select **Next**. An
   organization-controlled Workspace project may offer **Internal**, but only
   an administrator of that organization can decide whether it is appropriate.

![Audience selection with External selected](../images/gmail-authorization/03-external-audience.png)

4. Under **Contact Information**, enter **your own Google address**, then select
   **Next**.

![Contact Information with the developer email field](../images/gmail-authorization/04-contact-information.png)

5. Read Google's user-data policy. If you agree, select the checkbox and
   **Continue**.

![Finish step with the Google API Services User Data Policy checkbox](../images/gmail-authorization/05-user-data-policy.png)

6. When all four sections have blue check marks, select **Create**.

![Completed project configuration ready to create](../images/gmail-authorization/06-create-configuration.png)

7. Google returns to the OAuth Overview after creating the configuration.

![OAuth Overview confirming that the configuration was created](../images/gmail-authorization/07-configuration-created.png)

## Add initial test users

8. Open **Audience**. Under **Test users**, select **Add users** and add the
   maintainers and early users who will test the distributed client. Use their
   addresses, not a placeholder from the illustration.

![Audience page showing the Add users button](../images/gmail-authorization/08-add-test-user.png)

Google says that authorizations for External apps in **Testing** expire after
seven days. The app registration itself remains in place. See [Google's Audience
documentation](https://support.google.com/cloud/answer/15549945).

## The production barrier

9. Google may send you back to **Branding** before it will allow publication.
   The production form can require an application home page, privacy-policy
   link, authorized domain, and additional verification. Do not enter a domain
   you do not own or control.

![Branding page showing app-domain and developer-contact fields](../images/gmail-authorization/09-branding-requirements.png)

Publishing as **In production** allows any Google account to authorize without
being added as a test user. A personal-use app can remain unverified for fewer
than 100 users, but Google displays an unverified-app warning and applies a
lifetime 100-new-user cap. Verification is needed to remove that warning and
cap. See [Google's personal-use exception](https://support.google.com/cloud/answer/13464323).

## Add the read-only scope and Desktop client

Return to the terminal after each page:

1. On **Data Access**, add exactly
   `https://www.googleapis.com/auth/gmail.readonly`.
2. On **Clients**, create an OAuth client with application type **Desktop app**.
3. Download its JSON file and return to the terminal. Email Collection Toolkit finds the
   matching download, validates it, and prints where it saved the client.
4. For a release build, place that file at
   `src/mailarchiver/gmail_client.json`. It is a public Desktop-app client
   configuration and is packaged with Email Collection Toolkit. A development environment
   may instead set `MAILARCHIVER_GMAIL_CLIENT_JSON` to its path.
5. Complete the Google consent page for the maintainer's account to test it.

End users then run only `mailarchiver-auth THEIR_ADDRESS`. They never repeat
this registration procedure. Their tokens are stored in their own
operating-system credential stores and are not included in the distributed
client file or an archive.
