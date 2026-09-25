# Host your own backend (optional)

**You don't need this to use the plugin.** Snoots, the gobo gallery, beam
profiles and "Add your own…" gobos all run inside Blender with no account,
no key and no internet.

The backend is only for the **Lighting Note Assistant**. You type a note in
plain English, like *"snoot the key down so it stops spilling on the
background"*, and it proposes light settings for you to apply or discard.
That needs a language model. So it runs as a small service in **your own AWS
account**:

- your notes and audit log stay in your account;
- you pay AWS directly for what you use;
- you set the limits.

One command sets it up and prints the two things the plugin asks for: a
**Backend URL** and an **API key**.

## What you need

- An **AWS account** you can deploy to. An admin or power-user role is simplest.
  The deploy creates IAM roles, Lambda, API Gateway, DynamoDB, SSM, SNS and
  CloudWatch resources.
- **Amazon Bedrock access to Anthropic Claude Haiku 4.5** in the region you
  deploy to. In the Bedrock console, open the model catalog and make sure
  Claude Haiku 4.5 is available to your account. The first time an account
  uses an Anthropic model, AWS may ask for a short use-case description.
- These tools on macOS or Linux (on Windows, use WSL):
  - [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html),
    signed in, e.g. `aws sso login` or `aws configure`;
  - [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html);
  - [Go 1.24+](https://go.dev/dl/);
  - `git` and `make`.

  Docker is not needed.

## Deploy (about 5 minutes)

```bash
git clone https://github.com/marianina8/thornbury-lighting-assistant
cd thornbury-lighting-assistant
make self-host
```

That uses your default AWS credentials and **us-west-2**. To choose:

```bash
make self-host PROFILE=my-aws-profile REGION=eu-west-1
```

The model is picked to match the region: US and Canada regions use the `us.`
Bedrock profile, EU regions use `eu.`, and everywhere else uses `global.`.

It checks your tools and credentials, builds the Go service, deploys the
stack `thornbury-lighting-assistant`, and ends with:

```
  Backend URL:  https://abc123.execute-api.eu-west-1.amazonaws.com/demo
  API key:      tla_…
```

**The key is shown only once.** Only a hash of it is stored. If you lose it,
make another with `make issue-key LABEL=me`.

## Connect Blender

1. **Edit > Preferences > Add-ons > Thornbury Lighting Assistant**: paste the
   Backend URL and API key, then click **Test Connection**. You should see
   "✓ Connected as me: 0 of 50 used this month".
2. **Preferences > System > Network**: tick **Allow Online Access**.
3. Select a spot or area light, type a note in the **Lighting Note Assistant**
   panel, and click **Suggest**. Nothing changes until you click **Apply**.

## What it costs

- Each suggestion is one Claude Haiku 4.5 call: a few thousand tokens in and
  at most 400 out. That's typically well under a cent. See
  [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) for your region.
- Lambda, API Gateway and DynamoDB are pay-per-request. At this scale they
  are usually within the AWS free tier or cents a month.
- Nothing runs when nobody is using it, so an idle backend costs next to nothing.

**Built-in limits** (change them any time, see below):

| Limit | Default |
|---|---|
| Suggestions per key per month | 50 |
| Model calls per day, all keys together | 300 |
| Requests per second at the gateway | 5 (burst 10) |
| Kill switch trips if model calls in one hour exceed | 120 |

Requests with a missing or wrong key are refused before any model call.

Optional: add an AWS billing alarm that emails you over $10. First turn on
"Receive CloudWatch billing alerts" in the Billing console.

```bash
make billing-alarm EMAIL=you@example.com
```

## Day to day

```bash
make issue-key LABEL=teammate-name LIMIT=100      # a key for someone else
go run ./cmd/tla-admin keys                       # usage per key
go run ./cmd/tla-admin audit --n 20               # recent notes, proposals, outcomes
go run ./cmd/tla-admin revoke --id <key id>
go run ./cmd/tla-admin set-limit --id <key id> --limit 200
go run ./cmd/tla-admin pause | resume | status    # the kill switch
```

`tla-admin` uses your default AWS credentials and us-west-2. Add
`--profile` and `--region` if you deployed elsewhere.

**To change the limits,** redeploy with parameters, for example:

```bash
make self-host PARAMS="GlobalDailyLimit=100 ModelCallsPerHourAlarm=60"
```

Redeploying keeps your keys. It also mints one extra key labelled "me"; use
`make sam-deploy` instead to skip that.

**To remember your settings,** put them in a `local.mk` file in the repo.
It's git-ignored.

```make
PROFILE = my-aws-profile
REGION  = eu-west-1
```

## What gets stored

- **Keys table:** a hash of each key, its label, monthly limit and usage count.
- **Audit table (kept 180 days):** each note, the light's settings before, the
  model's raw answer, the clamped proposal, and whether you applied,
  edited or discarded it.

Nothing else from your scene is sent, and no images.

## Remove it

```bash
make self-host-delete                 # add PROFILE=/REGION= if you used them
```

This deletes the whole stack, including the keys and the audit log. If you
added the billing alarm, remove it separately:

```bash
aws cloudformation delete-stack --stack-name thornbury-lighting-assistant-billing --region us-east-1
```

## Troubleshooting

- **`AWS credentials aren't working`:** run `aws sso login` (or
  `aws configure`), or pass `PROFILE=`.
- **SSO "Token has expired and refresh failed":** run `aws sso login --profile <profile>` and try again.
- **The panel says the model call failed, and the audit shows `AccessDeniedException`:**
  Bedrock model access for Claude Haiku 4.5 isn't enabled in that region yet.
  Enable it in the Bedrock console. Failed calls don't count against the key's
  monthly limit.
- **Test Connection says online access is off:** Preferences > System >
  Network > Allow Online Access.
- **"The lighting assistant is paused by its owner":** the kill switch tripped on a usage spike.
  Check `tla-admin audit`, then run `tla-admin resume`.
- **Someone already deployed it in the same account and region:** deploy a
  second copy under another name, e.g.
  `make self-host STACK=my-tla PARAMS="Stage=mine"`. Pass the same `STACK=`
  and `PARAMS=` to later `make` commands, and `--stack my-tla` to `tla-admin`.
