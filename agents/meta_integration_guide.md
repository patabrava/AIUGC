# Meta Integration Guide

Use this guide when you need to reconnect or recreate the Meta login flow for FLOW-FORGE.

## Current Working Setup

- Meta app: `Lippe2`
- OAuth endpoint: `https://www.facebook.com/v25.0/dialog/oauth`
- Redirect URI: `https://aiugc-prod.srv1498567.hstgr.cloud/publish/meta/callback`
- Required scopes:
  - `pages_show_list`
  - `pages_read_engagement`
  - `pages_manage_posts`
  - `instagram_basic`
  - `instagram_content_publish`

## 1. Meta App Setup

1. Create or use an app that starts from the `Instagram API` product path.
2. Focus the app on Instagram-related permissions first.
3. In that Instagram app, open the `Facebook Login for Business` tab and finish the login setup there.
4. Add the production redirect URI exactly:
   - `https://aiugc-prod.srv1498567.hstgr.cloud/publish/meta/callback`
5. Make sure Web OAuth login is enabled.
6. Make sure HTTPS is enabled.
7. Make sure the app is in the correct mode for your test account.
8. Make sure the Facebook user you will test with is added as an app role, if the app is still in development.

## 1.1 Required Use Cases

People usually need these two use cases enabled in the Meta dashboard:

1. `Messaging and Content on Instagram verwalten`
2. `Alles auf deiner Seite verwalten`

Those are the two app use cases that line up with this integration:

- Instagram-side content and messaging permissions
- Facebook Page management for publishing through the connected Page

If either use case is missing, finish it before testing the login flow.

## 2. App Permissions

Request only the scopes that match the Facebook-login publishing flow:

```python
[
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "instagram_basic",
    "instagram_content_publish",
]
```

Do not request Instagram Login-only scopes in this flow:

- `instagram_business_basic`
- `instagram_business_content_publish`

Those cause the `Invalid Scopes` error when sent through the Facebook Login dialog.

If the app is still being configured, the useful mental model is:

- the app is Instagram-first
- the login flow is still Facebook Login for Business
- the permissions must match the endpoint you are using

## 3. Environment Variables

Set these on the production container or project environment:

```env
META_APP_ID=<lippe2_app_id>
META_APP_SECRET=<lippe2_app_secret>
META_REDIRECT_URI=https://aiugc-prod.srv1498567.hstgr.cloud/publish/meta/callback
```

Keep the redirect URI identical in:

- the Meta app settings
- the container environment
- the backend code

## 4. Backend Flow

1. The user clicks `Connect Meta`.
2. The app redirects to `facebook.com/dialog/oauth`.
3. The callback receives the OAuth `code`.
4. The backend exchanges the code for a Graph token.
5. The backend calls `GET /me/accounts`.
6. The user selects the correct Facebook Page.
7. The backend stores:
   - `page_id`
   - `page_name`
   - `page_access_token`
   - linked `instagram_business_account.id`

## 5. Publishing Flow

Use the Page token for Facebook publishing.

Use the Page-linked Instagram account for Instagram publishing.

Publishing should work like this:

1. Save the schedule.
2. Arm the post with `Confirm & Arm Dispatch`.
3. Let the scheduler publish when the time is due.
4. Store per-network results separately.

## 6. Production Deploy Steps

1. Update the code on `main`.
2. Push `main`.
3. Update the Hostinger Docker project environment.
4. Recreate the project containers.
5. Confirm `/publish/meta/connect` returns a `302` redirect to Meta.
6. Confirm the OAuth URL contains the expected scopes.
7. Complete the login in the browser.
8. If the app is wrong, fix the Meta app path first before changing code again.

## 7. Verification Checklist

- `GET /publish/meta/connect` returns a `302`
- OAuth URL includes the Facebook-login-compatible scopes
- OAuth uses the production redirect URI
- Login completes without `Invalid Scopes`
- `GET /me/accounts` returns the managed Page(s)
- A Page can be selected
- The linked Instagram business account appears
- Facebook and Instagram publish actions can be armed

## 8. Common Failures

### Invalid Scopes

Cause:

- The code is sending `instagram_business_*` scopes through the Facebook Login dialog.

Fix:

- Switch back to:
  - `pages_show_list`
  - `pages_read_engagement`
  - `pages_manage_posts`
  - `instagram_basic`
  - `instagram_content_publish`

### Wrong App ID

Cause:

- Production is still using the old Meta app credentials.

Fix:

- Update the Hostinger environment and recreate the containers.

### Redirect URI Error

Cause:

- The redirect URI in Meta does not exactly match the app environment.

Fix:

- Make sure the URI is identical everywhere.

### `me/accounts` Returns Nothing

Cause:

- The Facebook user does not manage a Page, or the Page is not linked to Instagram.

Fix:

- Use the Facebook account that manages the Page.
- Confirm the Page is linked to the Instagram professional account.

## 9. Quick Recovery Path

If the login breaks again, do this in order:

1. Check the app ID and secret in the container environment.
2. Check the redirect URI.
3. Check the scope list in the code.
4. Verify the app still starts from the Instagram API path.
5. Recreate the Docker project.
6. Retry the login from production.
