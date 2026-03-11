#!/bin/bash
# Deploy clawd-bot to EC2 via AWS CLI
set -euo pipefail
export AWS_PAGER=""

INSTANCE_TYPE="t4g.small"
KEY_NAME="clawd-bot-key"
SG_NAME="clawd-bot-sg"
INSTANCE_NAME="clawd-bot"
ROLE_NAME="clawd-bot-ec2-role"
PROFILE_NAME="clawd-bot-ec2-profile"
VAULT_REPO_URL="https://github.com/IslamTayeb/obsidian-vault.git"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "ERROR: Required command not found: $1"
        exit 1
    fi
}

require_env_or_gh() {
    local key="$1"
    if [ -n "${!key:-}" ]; then
        return
    fi

    if command -v gh >/dev/null 2>&1; then
        if [ "$key" = "GITHUB_USERNAME" ]; then
            export GITHUB_USERNAME
            GITHUB_USERNAME="$(gh api user -q .login)"
            return
        fi
        if [ "$key" = "GITHUB_TOKEN" ]; then
            export GITHUB_TOKEN
            GITHUB_TOKEN="$(gh auth token)"
            return
        fi
    fi

    echo "ERROR: $key is required in .env or via gh auth."
    exit 1
}

ssh_run() {
    ssh "${SSH_OPTS[@]}" "ec2-user@$PUBLIC_IP" "$@"
}

require_command aws
require_command curl
require_command python3
require_command ssh
require_command scp
require_command tar

echo "=== Clawd Bot EC2 Deployment ==="

if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "ERROR: .env file not found. Copy .env.example to .env and fill in values."
    exit 1
fi

set -a
# shellcheck disable=SC1091
source "$PROJECT_DIR/.env"
set +a

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
TRANSCRIBE_BUCKET="${TRANSCRIBE_BUCKET:-clawd-bot-transcribe-${ACCOUNT_ID}-${REGION}}"
TRANSCRIBE_VOCABULARY_NAME="${TRANSCRIBE_VOCABULARY_NAME:-clawd-bot-default-vocab}"
TRANSCRIBE_LANGUAGE_CODE="${TRANSCRIBE_LANGUAGE_CODE:-en-US}"
TRANSCRIBE_MODE="${TRANSCRIBE_MODE:-auto}"
TRANSCRIBE_AUTO_BATCH_MIN_SECONDS="${TRANSCRIBE_AUTO_BATCH_MIN_SECONDS:-90}"
BOT_TIMEZONE="${BOT_TIMEZONE:-America/New_York}"
CLAWD_MEMORY_PATH="${CLAWD_MEMORY_PATH:-personal/clawd.md}"
BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
require_env_or_gh GITHUB_USERNAME
require_env_or_gh GITHUB_TOKEN

telegram_ready=1
for required_var in TELEGRAM_TOKEN ALLOWED_USER_ID; do
    if [ -z "${!required_var:-}" ]; then
        telegram_ready=0
    fi
done

MY_IP="$(curl -fsSL https://checkip.amazonaws.com)"
MY_IP="${MY_IP}/32"
echo "Your IP: $MY_IP"

echo "=== Setting up Transcribe bucket ==="
if ! aws s3api head-bucket --bucket "$TRANSCRIBE_BUCKET" >/dev/null 2>&1; then
    if [ "$REGION" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "$TRANSCRIBE_BUCKET" --region "$REGION"
    else
        aws s3api create-bucket \
            --bucket "$TRANSCRIBE_BUCKET" \
            --region "$REGION" \
            --create-bucket-configuration "LocationConstraint=$REGION"
    fi
fi

aws s3api put-public-access-block \
    --bucket "$TRANSCRIBE_BUCKET" \
    --public-access-block-configuration \
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true' \
    --region "$REGION"

aws s3api put-bucket-encryption \
    --bucket "$TRANSCRIBE_BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
    --region "$REGION"

aws s3api put-bucket-lifecycle-configuration \
    --bucket "$TRANSCRIBE_BUCKET" \
    --lifecycle-configuration \
    '{"Rules":[{"ID":"expire-transcribe-temp","Filter":{"Prefix":"clawd-bot/"},"Status":"Enabled","Expiration":{"Days":1}}]}' \
    --region "$REGION"

echo "=== Setting up key pair ==="
if aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$REGION" >/dev/null 2>&1; then
    if [ ! -f "$PROJECT_DIR/$KEY_NAME.pem" ]; then
        echo "ERROR: AWS key pair $KEY_NAME exists, but $PROJECT_DIR/$KEY_NAME.pem is missing."
        exit 1
    fi
    chmod 600 "$PROJECT_DIR/$KEY_NAME.pem"
    echo "Key pair already exists: $KEY_NAME"
else
    aws ec2 create-key-pair \
        --key-name "$KEY_NAME" \
        --region "$REGION" \
        --query 'KeyMaterial' \
        --output text >"$PROJECT_DIR/$KEY_NAME.pem"
    chmod 600 "$PROJECT_DIR/$KEY_NAME.pem"
    echo "Created key pair: $KEY_NAME.pem"
fi

echo "=== Setting up security group ==="
SG_ID="$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$SG_NAME" \
    --region "$REGION" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || echo "None")"

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
    SG_ID="$(aws ec2 create-security-group \
        --group-name "$SG_NAME" \
        --description "Clawd bot - SSH access" \
        --region "$REGION" \
        --query 'GroupId' \
        --output text)"
    echo "Created security group: $SG_ID"
else
    echo "Security group exists: $SG_ID"
fi

aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" \
    --protocol tcp \
    --port 22 \
    --cidr "$MY_IP" \
    --region "$REGION" 2>/dev/null || true

echo "=== Setting up IAM role ==="
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }'

    echo "Created IAM role: $ROLE_NAME"
else
    echo "IAM role exists: $ROLE_NAME"
fi

aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "bedrock-access" \
    --policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            "Resource": "*"
        }]
    }'

aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "transcribe-access" \
    --policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "transcribe:StartStreamTranscription",
                "transcribe:StartTranscriptionJob",
                "transcribe:GetTranscriptionJob",
                "transcribe:DeleteTranscriptionJob"
            ],
            "Resource": "*"
        }]
    }'

aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "transcribe-s3-access" \
    --policy-document "{
        \"Version\": \"2012-10-17\",
        \"Statement\": [
            {
                \"Effect\": \"Allow\",
                \"Action\": [\"s3:ListBucket\"],
                \"Resource\": [\"arn:aws:s3:::${TRANSCRIBE_BUCKET}\"]
            },
            {
                \"Effect\": \"Allow\",
                \"Action\": [\"s3:GetObject\", \"s3:PutObject\", \"s3:DeleteObject\"],
                \"Resource\": [\"arn:aws:s3:::${TRANSCRIBE_BUCKET}/*\"]
            }
        ]
    }"

if ! aws iam get-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null 2>&1; then
    aws iam create-instance-profile --instance-profile-name "$PROFILE_NAME"
    aws iam add-role-to-instance-profile \
        --instance-profile-name "$PROFILE_NAME" \
        --role-name "$ROLE_NAME"
    echo "Created instance profile: $PROFILE_NAME"
    sleep 10
else
    echo "Instance profile exists: $PROFILE_NAME"
fi

echo "=== Syncing Transcribe vocabulary ==="
if [ -f "$PROJECT_DIR/transcribe_vocabulary.txt" ]; then
    vocabulary_phrases=()
    while IFS= read -r line; do
        vocabulary_phrases+=("$line")
    done < <(grep -v -E '^\s*(#|$)' "$PROJECT_DIR/transcribe_vocabulary.txt")

    if [ "${#vocabulary_phrases[@]}" -gt 0 ]; then
        if aws transcribe get-vocabulary \
            --vocabulary-name "$TRANSCRIBE_VOCABULARY_NAME" \
            --region "$REGION" >/dev/null 2>&1; then
            aws transcribe update-vocabulary \
                --vocabulary-name "$TRANSCRIBE_VOCABULARY_NAME" \
                --language-code "$TRANSCRIBE_LANGUAGE_CODE" \
                --phrases "${vocabulary_phrases[@]}" \
                --region "$REGION" >/dev/null
        else
            aws transcribe create-vocabulary \
                --vocabulary-name "$TRANSCRIBE_VOCABULARY_NAME" \
                --language-code "$TRANSCRIBE_LANGUAGE_CODE" \
                --phrases "${vocabulary_phrases[@]}" \
                --region "$REGION" >/dev/null
        fi

        state=""
        for attempt in $(seq 1 80); do
            state="$(aws transcribe get-vocabulary \
                --vocabulary-name "$TRANSCRIBE_VOCABULARY_NAME" \
                --region "$REGION" \
                --query 'VocabularyState' \
                --output text)"
            if [ "$state" = "READY" ]; then
                break
            fi
            if [ "$state" = "FAILED" ]; then
                reason="$(aws transcribe get-vocabulary \
                    --vocabulary-name "$TRANSCRIBE_VOCABULARY_NAME" \
                    --region "$REGION" \
                    --query 'FailureReason' \
                    --output text)"
                echo "WARNING: Transcribe vocabulary failed: $reason"
                break
            fi
            sleep 3
        done

        if [ "$state" != "READY" ]; then
            echo "WARNING: Transcribe vocabulary is not READY yet. The bot will continue and fall back until it is available."
        fi
    fi
fi

echo "=== Finding AMI ==="
AMI_ID="$(aws ec2 describe-images \
    --owners amazon \
    --filters \
        "Name=name,Values=al2023-ami-2023*-arm64" \
        "Name=state,Values=available" \
    --region "$REGION" \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
    --output text)"
echo "Using AMI: $AMI_ID"

EXISTING_ID="$(aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=$INSTANCE_NAME" "Name=instance-state-name,Values=running,stopped" \
    --region "$REGION" \
    --query 'Reservations[0].Instances[0].InstanceId' \
    --output text 2>/dev/null || echo "None")"

if [ "$EXISTING_ID" != "None" ] && [ -n "$EXISTING_ID" ]; then
    INSTANCE_ID="$EXISTING_ID"
    echo "Existing instance found: $INSTANCE_ID"

    STATE="$(aws ec2 describe-instances \
        --instance-ids "$INSTANCE_ID" \
        --region "$REGION" \
        --query 'Reservations[0].Instances[0].State.Name' \
        --output text)"
    if [ "$STATE" = "stopped" ]; then
        aws ec2 start-instances --instance-ids "$INSTANCE_ID" --region "$REGION"
        aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region "$REGION"
    fi
else
    echo "=== Launching EC2 instance ==="
    INSTANCE_ID="$(aws ec2 run-instances \
        --image-id "$AMI_ID" \
        --instance-type "$INSTANCE_TYPE" \
        --key-name "$KEY_NAME" \
        --security-group-ids "$SG_ID" \
        --iam-instance-profile Name="$PROFILE_NAME" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_NAME}]" \
        --region "$REGION" \
        --query 'Instances[0].InstanceId' \
        --output text)"
    echo "Launched instance: $INSTANCE_ID"
    aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region "$REGION"
fi

echo "=== Setting up Elastic IP ==="
EIP_ALLOC="$(aws ec2 describe-addresses \
    --filters "Name=tag:Name,Values=$INSTANCE_NAME" \
    --region "$REGION" \
    --query 'Addresses[0].AllocationId' \
    --output text 2>/dev/null || echo "None")"

if [ "$EIP_ALLOC" = "None" ] || [ -z "$EIP_ALLOC" ]; then
    EIP_ALLOC="$(aws ec2 allocate-address \
        --domain vpc \
        --region "$REGION" \
        --query 'AllocationId' \
        --output text)"
    aws ec2 create-tags \
        --resources "$EIP_ALLOC" \
        --tags "Key=Name,Value=$INSTANCE_NAME" \
        --region "$REGION"
fi

aws ec2 associate-address \
    --instance-id "$INSTANCE_ID" \
    --allocation-id "$EIP_ALLOC" \
    --region "$REGION" \
    --allow-reassociation

PUBLIC_IP="$(aws ec2 describe-addresses \
    --allocation-ids "$EIP_ALLOC" \
    --region "$REGION" \
    --query 'Addresses[0].PublicIp' \
    --output text)"
echo "Public IP: $PUBLIC_IP"

SSH_OPTS=(-o StrictHostKeyChecking=no -i "$PROJECT_DIR/$KEY_NAME.pem")

echo "=== Waiting for SSH ==="
ssh_ready=0
for attempt in $(seq 1 30); do
    if ssh "${SSH_OPTS[@]}" -o ConnectTimeout=5 "ec2-user@$PUBLIC_IP" "echo ok" >/dev/null 2>&1; then
        ssh_ready=1
        break
    fi
    echo "  Waiting... ($attempt/30)"
    sleep 10
done

if [ "$ssh_ready" -ne 1 ]; then
    echo "ERROR: SSH did not become available on $PUBLIC_IP."
    exit 1
fi

REMOTE_ENV_FILE="$(mktemp)"
trap 'rm -f "$REMOTE_ENV_FILE"' EXIT
grep -v -E '^(GITHUB_USERNAME|GITHUB_TOKEN|OBSIDIAN_VAULT)=' "$PROJECT_DIR/.env" >"$REMOTE_ENV_FILE"
{
    printf 'OBSIDIAN_VAULT=%s\n' "/home/ec2-user/obsidian-vault"
    printf 'BEDROCK_MODEL_ID=%s\n' "$BEDROCK_MODEL_ID"
    printf 'BOT_TIMEZONE=%s\n' "$BOT_TIMEZONE"
    printf 'CLAWD_MEMORY_PATH=%s\n' "$CLAWD_MEMORY_PATH"
    printf 'TRANSCRIBE_MODE=%s\n' "$TRANSCRIBE_MODE"
    printf 'TRANSCRIBE_AUTO_BATCH_MIN_SECONDS=%s\n' "$TRANSCRIBE_AUTO_BATCH_MIN_SECONDS"
    printf 'TRANSCRIBE_LANGUAGE_CODE=%s\n' "$TRANSCRIBE_LANGUAGE_CODE"
    printf 'TRANSCRIBE_BUCKET=%s\n' "$TRANSCRIBE_BUCKET"
    printf 'TRANSCRIBE_VOCABULARY_NAME=%s\n' "$TRANSCRIBE_VOCABULARY_NAME"
} >>"$REMOTE_ENV_FILE"

echo "=== Uploading project files ==="
ssh_run "mkdir -p ~/clawd-bot && find ~/clawd-bot -mindepth 1 -maxdepth 1 ! -name '.env' ! -name '.venv' -exec rm -rf {} +"
tar \
    --exclude='.git' \
    --exclude='.env' \
    --exclude='.venv' \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='*.pem' \
    --exclude='iphone-mirroring-screen.png' \
    --exclude='telegram-*.png' \
    -cf - \
    -C "$PROJECT_DIR" \
    . | ssh "${SSH_OPTS[@]}" "ec2-user@$PUBLIC_IP" "tar -xf - -C ~/clawd-bot"
scp "${SSH_OPTS[@]}" "$REMOTE_ENV_FILE" "ec2-user@$PUBLIC_IP:~/clawd-bot/.env"

echo "=== Running EC2 setup ==="
ssh_run "chmod +x ~/clawd-bot/setup_ec2.sh && sudo bash ~/clawd-bot/setup_ec2.sh"

GITHUB_CREDENTIAL_URL="$(python3 - <<'PY'
import os
import urllib.parse

username = urllib.parse.quote(os.environ["GITHUB_USERNAME"], safe="")
token = urllib.parse.quote(os.environ["GITHUB_TOKEN"], safe="")
print(f"https://{username}:{token}@github.com")
PY
)"

echo "=== Configuring GitHub access on EC2 ==="
ssh "${SSH_OPTS[@]}" "ec2-user@$PUBLIC_IP" <<EOF
set -euo pipefail
git config --global credential.helper store
umask 077
cat > /home/ec2-user/.git-credentials <<'CRED_EOF'
$GITHUB_CREDENTIAL_URL
CRED_EOF
EOF

echo "=== Syncing Obsidian vault ==="
ssh "${SSH_OPTS[@]}" "ec2-user@$PUBLIC_IP" <<EOF
set -euo pipefail
if [ -d /home/ec2-user/obsidian-vault ] && [ ! -d /home/ec2-user/obsidian-vault/.git ]; then
    echo "ERROR: /home/ec2-user/obsidian-vault exists but is not a git repo."
    exit 1
fi

if [ ! -d /home/ec2-user/obsidian-vault/.git ]; then
    git clone --branch main --single-branch "$VAULT_REPO_URL" /home/ec2-user/obsidian-vault
    git -C /home/ec2-user/obsidian-vault branch --set-upstream-to=origin/main main || true
else
    git -C /home/ec2-user/obsidian-vault remote set-url origin "$VAULT_REPO_URL"
    git -C /home/ec2-user/obsidian-vault branch --set-upstream-to=origin/main main || true
    git -C /home/ec2-user/obsidian-vault fetch origin main
    if ! git -C /home/ec2-user/obsidian-vault rebase --autostash origin/main; then
        git -C /home/ec2-user/obsidian-vault rebase --abort || true
        git -C /home/ec2-user/obsidian-vault merge --no-edit -X ours origin/main
    fi
fi
EOF

if [ "$telegram_ready" -eq 1 ]; then
    echo "=== Restarting bot ==="
    ssh_run "sudo systemctl restart clawd-bot && sudo systemctl status clawd-bot --no-pager"
else
    echo "=== Telegram bot service skipped ==="
    echo "TELEGRAM_TOKEN or ALLOWED_USER_ID is missing in $PROJECT_DIR/.env."
    ssh_run "systemctl is-enabled clawd-bot || true"
fi

echo
echo "=== Deployment complete ==="
echo "Instance ID: $INSTANCE_ID"
echo "Public IP:   $PUBLIC_IP"
echo "SSH:         ssh -i $PROJECT_DIR/$KEY_NAME.pem ec2-user@$PUBLIC_IP"
echo "Logs:        ssh -i $PROJECT_DIR/$KEY_NAME.pem ec2-user@$PUBLIC_IP 'sudo journalctl -u clawd-bot -f'"
