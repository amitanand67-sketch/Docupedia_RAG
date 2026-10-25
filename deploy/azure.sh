#!/usr/bin/env bash
# Deploy DocupediaAI to Azure. Every setting lives in .env.azure (copy it from .env.azure.example).
#
#   ./deploy/azure.sh create     create Search, Cosmos DB, registry and Container Apps environment
#   ./deploy/azure.sh ingest     index sample_docs into Azure AI Search (runs on your laptop)
#   ./deploy/azure.sh deploy     build the image and start (or update) the web app; prints the link
#   ./deploy/azure.sh status     show the link and what is running
#   ./deploy/azure.sh logs       stream the app's logs (Ctrl+C to stop)
#   ./deploy/azure.sh signin     optional: require Microsoft sign-in ("signin off" to undo)
#   ./deploy/azure.sh delete     delete everything this script created
#
# Works with the bash that ships with macOS (3.2). Needs the Azure CLI (az) and uv.

set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=.env.azure

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }
exists() { "$@" >/dev/null 2>&1; }

confirm() {
  printf '%s [y/N] ' "$1"
  read -r answer
  case "$answer" in y | Y | yes | YES) ;; *) echo "Stopped, nothing changed."; exit 0 ;; esac
}

# Write KEY=value into .env.azure, replacing the line if it is already there.
set_env() {
  local tmp
  tmp=$(mktemp)
  awk -v k="$1" -v v="$2" '
    index($0, k "=") == 1 { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$ENV_FILE" >"$tmp"
  mv "$tmp" "$ENV_FILE"
  export "$1=$2"
}

load_env() {
  if [ ! -f "$ENV_FILE" ]; then
    cp .env.azure.example "$ENV_FILE"
    die "Created $ENV_FILE from the example. Fill in section 1 (your Azure OpenAI key), then run this again."
  fi
  set -a
  # shellcheck disable=SC1090
  . "./$ENV_FILE"
  set +a
  [ -n "${AZURE_OPENAI_API_KEY:-}" ] || die "AZURE_OPENAI_API_KEY is empty in $ENV_FILE (copy it from your .env)."
  [ -n "${APP_NAME:-}" ] || die "APP_NAME is empty in $ENV_FILE."

  if [ -z "${NAME_SUFFIX:-}" ]; then  # makes globally unique names; generated once and kept
    set_env NAME_SUFFIX "$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 5)"
  fi
  RG=$AZURE_RESOURCE_GROUP
  LOC=$AZURE_LOCATION
  SEARCH_NAME="$APP_NAME-search-$NAME_SUFFIX"
  COSMOS_NAME="$APP_NAME-cosmos-$NAME_SUFFIX"
  DOCINTEL_NAME="$APP_NAME-docintel-$NAME_SUFFIX"
  ACR_NAME="${APP_NAME}acr$NAME_SUFFIX"
  ENV_NAME="$APP_NAME-env"
  APP="$APP_NAME-app"
}

check_login() {
  command -v az >/dev/null || die "Azure CLI not found. Install it: brew install azure-cli"
  az account show >/dev/null 2>&1 || die "Not signed in. Run: az login"
  echo "Subscription: $(az account show --query name -o tsv)"
  az extension add --name containerapp --upgrade --only-show-errors -y >/dev/null 2>&1 || true
}

cmd_create() {
  check_login
  cat <<EOF

This creates, in resource group $RG ($LOC):
  - Azure AI Search      $SEARCH_NAME   Free tier          \$0
  - Cosmos DB for NoSQL  $COSMOS_NAME   free tier discount \$0 (1000 RU/s and 25 GB free)
  - Container Registry   $ACR_NAME      Basic              about \$0.17 a day (\$5 a month)
  - Container Apps env   $ENV_NAME      consumption plan   \$0 while idle
EOF
  [ "${WITH_DOCINTEL:-false}" = true ] && echo "  - Document Intelligence $DOCINTEL_NAME S0             \$0.01 per page"
  echo
  confirm "Create these resources?"

  say "Registering resource providers (first time only, takes a minute)"
  for ns in Microsoft.App Microsoft.ContainerRegistry Microsoft.Search Microsoft.DocumentDB \
    Microsoft.CognitiveServices Microsoft.OperationalInsights; do
    az provider register --namespace "$ns" --wait --only-show-errors
  done

  say "Resource group $RG"
  az group create --name "$RG" --location "$LOC" --only-show-errors -o none

  say "Azure AI Search (Free)"
  if ! exists az search service show --name "$SEARCH_NAME" --resource-group "$RG"; then
    az search service create --name "$SEARCH_NAME" --resource-group "$RG" --location "$LOC" \
      --sku free --only-show-errors -o none ||
      die "Search creation failed. If it says a free service already exists, you can only have one per subscription; if the region has no capacity, change AZURE_LOCATION (e.g. eastus) and run create again."
  fi
  set_env AZURE_SEARCH_ENDPOINT "https://$SEARCH_NAME.search.windows.net"
  set_env AZURE_SEARCH_API_KEY "$(az search admin-key show --service-name "$SEARCH_NAME" --resource-group "$RG" --query primaryKey -o tsv)"

  say "Cosmos DB with free tier (this one takes 5 to 10 minutes)"
  if ! exists az cosmosdb show --name "$COSMOS_NAME" --resource-group "$RG"; then
    az cosmosdb create --name "$COSMOS_NAME" --resource-group "$RG" --enable-free-tier true \
      --locations regionName="$LOC" failoverPriority=0 isZoneRedundant=False \
      --default-consistency-level Session --only-show-errors -o none ||
      die "Cosmos DB creation failed. Free tier is allowed on one account per subscription."
  fi
  # 400 RU/s shared by the database stays inside the 1000 RU/s free allowance.
  if ! exists az cosmosdb sql database show --account-name "$COSMOS_NAME" --resource-group "$RG" --name docupedia; then
    az cosmosdb sql database create --account-name "$COSMOS_NAME" --resource-group "$RG" \
      --name docupedia --throughput 400 --only-show-errors -o none
  fi
  if ! exists az cosmosdb sql container show --account-name "$COSMOS_NAME" --resource-group "$RG" \
    --database-name docupedia --name conversations; then
    az cosmosdb sql container create --account-name "$COSMOS_NAME" --resource-group "$RG" \
      --database-name docupedia --name conversations --partition-key-path /session_id --only-show-errors -o none
  fi
  set_env COSMOS_ENDPOINT "$(az cosmosdb show --name "$COSMOS_NAME" --resource-group "$RG" --query documentEndpoint -o tsv)"
  set_env COSMOS_KEY "$(az cosmosdb keys list --name "$COSMOS_NAME" --resource-group "$RG" --query primaryMasterKey -o tsv)"

  if [ "${WITH_DOCINTEL:-false}" = true ]; then
    say "Document Intelligence (${DOCINTEL_SKU:-S0})"
    if ! exists az cognitiveservices account show --name "$DOCINTEL_NAME" --resource-group "$RG"; then
      az cognitiveservices account create --name "$DOCINTEL_NAME" --resource-group "$RG" --location "$LOC" \
        --kind FormRecognizer --sku "${DOCINTEL_SKU:-S0}" --custom-domain "$DOCINTEL_NAME" --yes --only-show-errors -o none
    fi
    set_env AZURE_DOCINTEL_ENDPOINT "$(az cognitiveservices account show --name "$DOCINTEL_NAME" --resource-group "$RG" --query properties.endpoint -o tsv)"
    set_env AZURE_DOCINTEL_API_KEY "$(az cognitiveservices account keys list --name "$DOCINTEL_NAME" --resource-group "$RG" --query key1 -o tsv)"
  fi

  say "Container Registry (Basic)"
  if ! exists az acr show --name "$ACR_NAME" --resource-group "$RG"; then
    az acr create --name "$ACR_NAME" --resource-group "$RG" --location "$LOC" --sku Basic \
      --admin-enabled true --only-show-errors -o none
  fi

  say "Container Apps environment (logs stream live; no Log Analytics workspace to pay for)"
  if ! exists az containerapp env show --name "$ENV_NAME" --resource-group "$RG"; then
    az containerapp env create --name "$ENV_NAME" --resource-group "$RG" --location "$LOC" \
      --logs-destination none --only-show-errors -o none
  fi

  say "Done. Keys were saved in $ENV_FILE. Next: ./deploy/azure.sh ingest"
}

cmd_ingest() {
  [ -n "${AZURE_SEARCH_ENDPOINT:-}" ] || die "Run ./deploy/azure.sh create first."
  say "Indexing sample_docs into Azure AI Search ($AZURE_SEARCH_INDEX)"
  ENV_FILE=.env.azure uv run python scripts/ingest.py sample_docs "$@"
}

build_image() {
  IMAGE="$ACR_NAME.azurecr.io/docupedia:$(date +%Y%m%d%H%M%S)"
  say "Building the image in Azure (ACR Tasks)"
  if az acr build --registry "$ACR_NAME" --image "${IMAGE#*/}" --platform linux/amd64 . --only-show-errors; then
    return
  fi
  # Free-credit subscriptions often can't use ACR Tasks; build on this Mac instead.
  say "Cloud build unavailable, building locally with Docker"
  command -v docker >/dev/null || die "Install Docker Desktop (https://www.docker.com/products/docker-desktop), start it, and run deploy again."
  docker build --platform linux/amd64 -t "$IMAGE" .
  az acr login --name "$ACR_NAME"
  docker push "$IMAGE"
}

cmd_deploy() {
  check_login
  [ -n "${COSMOS_KEY:-}" ] || die "Run ./deploy/azure.sh create first."
  build_image

  local server user password
  server="$ACR_NAME.azurecr.io"
  user=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)
  password=$(az acr credential show --name "$ACR_NAME" --query 'passwords[0].value' -o tsv)

  # Keys go in as Container Apps secrets; the app's environment only references them.
  secrets=(aoai-key="$AZURE_OPENAI_API_KEY" search-key="$AZURE_SEARCH_API_KEY" cosmos-key="$COSMOS_KEY")
  envs=(
    AZURE_OPENAI_ENDPOINT="$AZURE_OPENAI_ENDPOINT"
    AZURE_OPENAI_API_KEY=secretref:aoai-key
    AZURE_OPENAI_CHAT_DEPLOYMENT="$AZURE_OPENAI_CHAT_DEPLOYMENT"
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT="$AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
    AZURE_OPENAI_API_VERSION="$AZURE_OPENAI_API_VERSION"
    EMBEDDING_DIMENSIONS="$EMBEDDING_DIMENSIONS"
    SEARCH_BACKEND="$SEARCH_BACKEND"
    AZURE_SEARCH_ENDPOINT="$AZURE_SEARCH_ENDPOINT"
    AZURE_SEARCH_API_KEY=secretref:search-key
    AZURE_SEARCH_INDEX="$AZURE_SEARCH_INDEX"
    USE_SEMANTIC_RANKER="$USE_SEMANTIC_RANKER"
    HISTORY_BACKEND="$HISTORY_BACKEND"
    COSMOS_ENDPOINT="$COSMOS_ENDPOINT"
    COSMOS_KEY=secretref:cosmos-key
    AUTH_MODE="$AUTH_MODE"
    RATE_LIMIT_PER_HOUR="$RATE_LIMIT_PER_HOUR"
    DAILY_QUESTION_LIMIT="$DAILY_QUESTION_LIMIT"
  )
  [ -n "${ADMIN_USERS:-}" ] && envs+=(ADMIN_USERS="$ADMIN_USERS")

  if exists az containerapp show --name "$APP" --resource-group "$RG"; then
    say "Updating $APP"
    az containerapp secret set --name "$APP" --resource-group "$RG" --secrets "${secrets[@]}" --only-show-errors -o none
    az containerapp update --name "$APP" --resource-group "$RG" --image "$IMAGE" \
      --min-replicas 0 --max-replicas 1 --set-env-vars "${envs[@]}" --only-show-errors -o none
  else
    say "Creating $APP"
    az containerapp create --name "$APP" --resource-group "$RG" --environment "$ENV_NAME" \
      --image "$IMAGE" --registry-server "$server" --registry-username "$user" --registry-password "$password" \
      --target-port 8000 --ingress external --min-replicas 0 --max-replicas 1 --cpu 0.5 --memory 1.0Gi \
      --secrets "${secrets[@]}" --env-vars "${envs[@]}" --only-show-errors -o none
  fi

  set_env APP_URL "https://$(az containerapp show --name "$APP" --resource-group "$RG" --query properties.configuration.ingress.fqdn -o tsv)"
  say "Checking $APP_URL/health (the first request wakes the app, so it can take up to a minute)"
  curl -fsS --retry 6 --retry-delay 10 --retry-all-errors --max-time 60 "$APP_URL/health" && echo
  say "Live at $APP_URL"
}

cmd_status() {
  check_login
  echo "Link:      ${APP_URL:-not deployed yet}"
  if exists az containerapp show --name "$APP" --resource-group "$RG"; then
    az containerapp show --name "$APP" --resource-group "$RG" -o table \
      --query '{image: properties.template.containers[0].image, running: properties.runningStatus, public: properties.configuration.ingress.external}'
    echo "Replicas running now: $(az containerapp replica list --name "$APP" --resource-group "$RG" --query 'length(@)' -o tsv 2>/dev/null || echo 0) (0 = asleep, wakes on the next visit)"
  fi
  echo
  az resource list --resource-group "$RG" --query '[].{name:name, type:type}' -o table
}

cmd_logs() {
  az containerapp logs show --name "$APP" --resource-group "$RG" --follow --tail 50
}

cmd_signin() {
  check_login
  [ -n "${APP_URL:-}" ] || die "Run ./deploy/azure.sh deploy first."
  if [ "${1:-}" = off ]; then
    say "Turning sign-in off (back to the demo user picker)"
    az containerapp auth update --name "$APP" --resource-group "$RG" --enabled false --only-show-errors -o none
    az containerapp update --name "$APP" --resource-group "$RG" --set-env-vars AUTH_MODE=demo --only-show-errors -o none
    set_env AUTH_MODE demo
    return
  fi
  cat <<EOF

Visitors will have to sign in with an account in your Azure directory. Interviewers outside it
can't get in unless you invite them as guests (Entra ID > Users > Invite external user).
EOF
  confirm "Require sign-in?"

  local tenant app_id secret
  tenant=$(az account show --query tenantId -o tsv)
  if [ -z "${ENTRA_APP_ID:-}" ]; then
    say "Registering the app in Entra ID"
    set_env ENTRA_APP_ID "$(az ad app create --display-name "$APP_NAME-signin" \
      --web-redirect-uris "$APP_URL/.auth/login/aad/callback" --enable-id-token-issuance true \
      --sign-in-audience AzureADMyOrg --query appId -o tsv)"
  fi
  app_id=$ENTRA_APP_ID
  secret=$(az ad app credential reset --id "$app_id" --append --display-name easyauth --years 1 --query password -o tsv)

  say "Turning on Container Apps authentication"
  az containerapp secret set --name "$APP" --resource-group "$RG" \
    --secrets microsoft-provider-authentication-secret="$secret" --only-show-errors -o none
  az containerapp auth microsoft update --name "$APP" --resource-group "$RG" --client-id "$app_id" \
    --client-secret-name microsoft-provider-authentication-secret \
    --issuer "https://login.microsoftonline.com/$tenant/v2.0" --yes --only-show-errors -o none
  az containerapp auth update --name "$APP" --resource-group "$RG" --enabled true \
    --unauthenticated-client-action RedirectToLoginPage --redirect-provider azureactivedirectory \
    --only-show-errors -o none
  az containerapp update --name "$APP" --resource-group "$RG" --set-env-vars AUTH_MODE=entra \
    ${ADMIN_USERS:+ADMIN_USERS="$ADMIN_USERS"} --only-show-errors -o none
  set_env AUTH_MODE entra
  say "Sign-in is on. Open $APP_URL in a private window to try it."
  [ -n "${ADMIN_USERS:-}" ] || echo "Tip: put your sign-in email in ADMIN_USERS and run deploy, to see the Analytics tab."
}

cmd_delete() {
  check_login
  local models
  models=$(az resource list --resource-group "$RG" \
    --query "[?type=='Microsoft.CognitiveServices/accounts' && (kind=='OpenAI' || kind=='AIServices')] | length(@)" -o tsv)
  [ "${models:-0}" = 0 ] || die "$RG contains an Azure OpenAI resource. Refusing to delete it; delete the app resources one by one instead."
  echo
  az resource list --resource-group "$RG" --query '[].{name:name, type:type}' -o table
  printf '\nThis permanently deletes everything above. Type the resource group name (%s) to confirm: ' "$RG"
  read -r typed
  [ "$typed" = "$RG" ] || { echo "Names don't match, nothing deleted."; exit 0; }
  az group delete --name "$RG" --yes --no-wait
  if [ -n "${ENTRA_APP_ID:-}" ]; then
    az ad app delete --id "$ENTRA_APP_ID" && set_env ENTRA_APP_ID ""
  fi
  for key in AZURE_SEARCH_ENDPOINT AZURE_SEARCH_API_KEY AZURE_DOCINTEL_ENDPOINT AZURE_DOCINTEL_API_KEY \
    COSMOS_ENDPOINT COSMOS_KEY APP_URL; do
    set_env "$key" ""
  done
  say "Deleting in the background (a few minutes). Your Azure OpenAI resource is untouched."
}

command=${1:-}
[ $# -gt 0 ] && shift
case "$command" in
  create | ingest | deploy | status | logs | signin | delete)
    load_env
    "cmd_$command" "$@"
    ;;
  *)
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
