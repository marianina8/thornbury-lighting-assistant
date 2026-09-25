# Thornbury Lighting Assistant (demo 7)
#
#   make test            Go tests (race detector) + vet
#   make local           run the API locally with the mock model (no AWS)
#   make local-bedrock   run the API locally against real Bedrock (your AWS profile)
#   make addon-zip       build dist/thornbury_lighting-<version>.zip (Install from Disk)
#   make addon-test      addon tests inside real Blender builds (needs bpy wheels; see README)
#   make self-host       deploy your own backend and print your URL + key (docs/self-host.md)
#   make self-host-delete  remove that backend again
#   make sam-deploy      build + deploy the stack
#   make issue-key LABEL=boyfriend-tester LIMIT=50
#   make billing-alarm EMAIL=you@example.com
#
# AWS credentials come from the usual places (AWS_PROFILE, SSO, env vars).
# PROFILE=... and REGION=... override them; put personal defaults in local.mk
# (git-ignored), e.g.  PROFILE = my-profile

-include local.mk

STACK   ?= thornbury-lighting-assistant
PROFILE ?= $(AWS_PROFILE)
REGION  ?= us-west-2
# Bedrock cross-region inference profile for Claude Haiku 4.5, picked from the
# region: us.* for US/Canada regions, eu.* for EU regions, global.* elsewhere.
MODEL_ID ?= $(if $(filter us-% ca-%,$(REGION)),us,$(if $(filter eu-%,$(REGION)),eu,global)).anthropic.claude-haiku-4-5-20251001-v1:0
# Extra stack parameters, e.g. PARAMS="GlobalDailyLimit=100"
PARAMS ?=
AWSFLAGS = $(if $(PROFILE),--profile $(PROFILE)) --region $(REGION)
LABEL   ?=
LIMIT   ?= 50
EMAIL   ?=
VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' addon/thornbury_lighting/blender_manifest.toml)
ZIP     := dist/thornbury_lighting-$(VERSION).zip
# Blender builds used by addon-test: python interpreters that have the `bpy` wheel.
BLENDER_PYS ?= $(wildcard .venvs/bpy-*/bin/python)

.PHONY: test vet local local-bedrock addon-zip addon-test render-check presets sam-build sam-deploy issue-key billing-alarm lint self-host self-host-check self-host-delete

test: vet
	go test -race ./...

vet:
	go vet ./...

local:
	go run ./cmd/local

local-bedrock:
	go run ./cmd/local -model bedrock -profile "$(PROFILE)" -region $(REGION) -model-id $(MODEL_ID)

# Same layout as `blender --command extension build`; no Blender needed.
addon-zip:
	python3 tools/build_addon.py

addon-test: addon-zip
	@test -n "$(BLENDER_PYS)" || (echo "No Blender builds found. See README > Testing the addon."; exit 1)
	@for py in $(BLENDER_PYS); do \
		echo "== $$py"; \
		BLENDER_USER_RESOURCES=$$(mktemp -d) $$py addon/tests/test_addon.py || exit 1; \
	done

render-check:
	@for py in $(BLENDER_PYS); do echo "== $$py"; $$py tools/render_presets.py && $$py tools/render_snoots.py || exit 1; done

presets:
	python3 tools/gen_presets.py

# sam build calls these (BuildMethod: makefile). One Go binary, two handlers.
build-ApiFunction build-KillSwitchFunction:
	GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -tags lambda.norpc -trimpath -ldflags="-s -w" -o $(ARTIFACTS_DIR)/bootstrap ./cmd/api

sam-build:
	sam build --template-file infra/template.yaml

sam-deploy: sam-build
	sam deploy --template-file .aws-sam/build/template.yaml --stack-name $(STACK) \
		$(AWSFLAGS) --capabilities CAPABILITY_IAM --resolve-s3 \
		--parameter-overrides ModelId=$(MODEL_ID) $(PARAMS) \
		--tags app=thornbury-lighting-assistant data=synthetic --no-fail-on-empty-changeset

issue-key:
	@test -n "$(LABEL)" || (echo "usage: make issue-key LABEL=boyfriend-tester [LIMIT=50]"; exit 2)
	go run ./cmd/issue-key --label "$(LABEL)" --monthly-limit $(LIMIT) --stack $(STACK) --profile "$(PROFILE)" --region $(REGION)

# ---- Host your own backend (optional; snoots and gobos never need it) ----
self-host-check:
	@for t in go sam aws; do command -v $$t >/dev/null || { echo "Missing '$$t'. See docs/self-host.md > What you need."; exit 1; }; done
	@aws sts get-caller-identity $(AWSFLAGS) --query Account --output text >/dev/null 2>&1 || \
		{ echo "AWS credentials aren't working (try 'aws sso login' or 'aws configure'). See docs/self-host.md."; exit 1; }
	@echo "Deploying stack $(STACK) to $(REGION) in account $$(aws sts get-caller-identity $(AWSFLAGS) --query Account --output text), model $(MODEL_ID)"

self-host: self-host-check sam-deploy
	@$(MAKE) --no-print-directory issue-key LABEL="$(or $(LABEL),me)"

self-host-delete:
	sam delete --stack-name $(STACK) $(AWSFLAGS) --no-prompts

billing-alarm:
	@test -n "$(EMAIL)" || (echo "usage: make billing-alarm EMAIL=you@example.com"; exit 2)
	aws cloudformation deploy --template-file infra/billing-alarm.yaml --stack-name $(STACK)-billing \
		--parameter-overrides Email=$(EMAIL) --region us-east-1 $(if $(PROFILE),--profile $(PROFILE)) \
		--tags app=thornbury-lighting-assistant data=synthetic

lint:
	cfn-lint infra/template.yaml infra/billing-alarm.yaml
