# Thornbury Lighting Assistant (demo 7)
#
#   make test            Go tests (race detector) + vet
#   make local           run the API locally with the mock model (no AWS)
#   make local-bedrock   run the API locally against real Bedrock (your AWS profile)
#   make addon-zip       build dist/thornbury_lighting-<version>.zip (Install from Disk)
#   make addon-test      addon tests inside real Blender builds (needs bpy wheels; see README)
#   make sam-deploy      build + deploy the stack (us-west-2, profile demos-admin)
#   make issue-key LABEL=boyfriend-tester LIMIT=50
#   make billing-alarm EMAIL=you@example.com

STACK   ?= thornbury-lighting-assistant
PROFILE ?= demos-admin
REGION  ?= us-west-2
LABEL   ?=
LIMIT   ?= 50
EMAIL   ?=
VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' addon/thornbury_lighting/blender_manifest.toml)
ZIP     := dist/thornbury_lighting-$(VERSION).zip
# Blender builds used by addon-test: python interpreters that have the `bpy` wheel.
BLENDER_PYS ?= $(wildcard .venvs/bpy-*/bin/python)

.PHONY: test vet local local-bedrock addon-zip addon-test render-check presets sam-build sam-deploy issue-key billing-alarm lint

test: vet
	go test -race ./...

vet:
	go vet ./...

local:
	go run ./cmd/local

local-bedrock:
	go run ./cmd/local -model bedrock -profile $(PROFILE) -region $(REGION)

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
		--region $(REGION) --profile $(PROFILE) --capabilities CAPABILITY_IAM --resolve-s3 \
		--tags app=thornbury-lighting-assistant data=synthetic --no-fail-on-empty-changeset

issue-key:
	@test -n "$(LABEL)" || (echo "usage: make issue-key LABEL=boyfriend-tester [LIMIT=50]"; exit 2)
	go run ./cmd/issue-key --label "$(LABEL)" --monthly-limit $(LIMIT) --stack $(STACK) --profile $(PROFILE) --region $(REGION)

billing-alarm:
	@test -n "$(EMAIL)" || (echo "usage: make billing-alarm EMAIL=you@example.com"; exit 2)
	aws cloudformation deploy --template-file infra/billing-alarm.yaml --stack-name $(STACK)-billing \
		--parameter-overrides Email=$(EMAIL) --region us-east-1 --profile $(PROFILE) \
		--tags app=thornbury-lighting-assistant data=synthetic

lint:
	cfn-lint infra/template.yaml infra/billing-alarm.yaml
