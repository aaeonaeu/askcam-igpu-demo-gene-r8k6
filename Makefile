.PHONY: up down logs ps bundle restart-vlm test
up:        ## start everything (build askcam-ui if needed)
	docker compose up -d --build
down:      ## stop everything (data in models/ media/ frigate/ is kept)
	docker compose down
logs:      ## follow all logs
	docker compose logs -f --tail=100
ps:
	docker compose ps
restart-vlm: ## after editing vlm/models.ini
	docker compose restart vlm
bundle:    ## offline bundle (3B model only; use scripts/bundle-offline.sh --all-models for all)
	scripts/bundle-offline.sh
test:      ## ask 9 questions through the UI and report latency / skipped frames
	python3 scripts/ui-test.py 3
