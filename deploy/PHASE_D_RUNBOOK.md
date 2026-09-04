# Phase D — AWS/EKS infrastructure runbook

You run every command in this file on your own machine, with your own AWS
credentials. Paste output back (especially errors) for debugging.

**Scope of Phase D**: get the cluster, the WireGuard tunnel to gx10.lan, and
the ALB controller up and *proven* — no app pods yet. Phase E is a separate
session: the Helm chart, the actual Deployments, and hitting `/healthz`
through a real ALB hostname.

## Why the tunnel-server lives inside the EKS VPC (read before Step 2)

Earlier revisions of this runbook put the WireGuard tunnel-server in the
account's **default VPC**, peered to the EKS VPC, specifically so it could be
set up once and never touched again regardless of how many times the EKS
cluster gets torn down and recreated between sessions. **That doesn't work —
found the hard way in the 2026-08-30/09-02 session.** AWS VPC peering
connections do not support "edge-to-edge routing through a NAT instance":
a peering connection will only deliver traffic destined to an address
*within the peer VPC's own registered CIDR block*. The tunnel-server acts as
a NAT/router instance forwarding traffic onward to `10.100.0.0/24` — a
destination that only exists via the tunnel-server's own WireGuard routing,
not as a real subnet of the VPC it lives in. AWS silently accepts a route
table entry pointing at it and then never actually delivers the traffic;
there is no config fix, it's a hard platform limitation. Confirmed via a
`tcpdump` on the tunnel-server's real interface showing zero packets ever
arriving, while the same capture on the *node* showed the packet correctly
leaving with the right destination.

**Consequence for lifecycle/cost**: the tunnel-server now has to live inside
the EKS cluster's own VPC, so it gets torn down along with the cluster
between sessions — it's no longer a "set up once, forget about it" piece.
The mitigation: **preserve the WireGuard private key and peer public keys
somewhere outside the VPC** (a text note, this file's own history, etc.) —
relaunching each session is then "new instance + paste the same keys back
in", not "regenerate everything and reconfigure gx10.lan again". Steps below
call out exactly which values to save.

---

## 0. Prerequisites

```bash
aws --version && eksctl version && kubectl version --client && helm version

aws configure   # needs an IAM user/role with broad-ish permissions (EC2,
                 # EKS, IAM, ECR, VPC); AdministratorAccess is fine for a
                 # learning project, a scoped policy is the "real" version

export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export EKS_CLUSTER_NAME=codereview
echo "Account: $AWS_ACCOUNT_ID   Region: $AWS_REGION"
```

**Gotcha**: check whether `aws --version` reports v1 or v2 — if v1, and
you want v2, first check whether v2 is *already* installed but shadowed by
a `pip`-installed v1 earlier on `PATH` (`which -a aws`) before doing a fresh
install; running the official installer's `--update` flag repairs an
existing v2 install without redownloading.

---

## 1. Create the EKS cluster ⚠️ cost clock starts here

This is the ~$150-200/mo part if left running (control plane + 2 nodes +
whatever load balancers Phase E adds). Takes ~15-20 minutes.

```bash
eksctl create cluster \
  --name $EKS_CLUSTER_NAME \
  --region $AWS_REGION \
  --vpc-cidr 10.0.0.0/16 \
  --nodegroup-name standard-workers \
  --node-type t3.small \
  --nodes 2 --nodes-min 2 --nodes-max 2 \
  --managed
```

`--vpc-cidr 10.0.0.0/16` is explicit rather than eksctl's default
(`192.168.0.0/16`), so nothing AWS-side ever shares a prefix with your home
LAN (`192.168.4.0/24`) or the WireGuard overlay (`10.100.0.0/24`) — three
separate, non-overlapping ranges.

`t3.small`, not `t3.medium`: this AWS account has a new-account guardrail
restricting `RunInstances` (including the ASG a managed node group launches
under the hood) to free-tier-eligible types only. Check yours with:

```bash
aws ec2 describe-instance-types --region $AWS_REGION --filters Name=free-tier-eligible,Values=true --query 'InstanceTypes[].InstanceType' --output text
```

**If the node group fails with the free-tier error**: the cluster/control
plane stack itself will likely still succeed (only the node group's ASG
launches EC2 instances). The failed node group's CloudFormation stack lands
in `ROLLBACK_COMPLETE`, which can't be reused — and `eksctl` enables
termination protection on its stacks by default, so deleting it needs one
extra step first:

```bash
aws cloudformation update-termination-protection --region $AWS_REGION --stack-name eksctl-$EKS_CLUSTER_NAME-nodegroup-standard-workers --no-enable-termination-protection
aws cloudformation delete-stack --region $AWS_REGION --stack-name eksctl-$EKS_CLUSTER_NAME-nodegroup-standard-workers
aws cloudformation wait stack-delete-complete --region $AWS_REGION --stack-name eksctl-$EKS_CLUSTER_NAME-nodegroup-standard-workers

# control plane already exists — just add a correctly-typed node group:
eksctl create nodegroup --cluster $EKS_CLUSTER_NAME --region $AWS_REGION --name standard-workers --node-type t3.small --nodes 2 --nodes-min 2 --nodes-max 2 --managed
```

Verify:

```bash
kubectl get nodes
kubectl get pods -A
```

Both nodes should show `Ready`; `kube-system` pods should all be `Running`.
**Gotcha**: `eksctl`'s kubeconfig auto-write only applies to the shell
session it ran in — a different terminal will show `dial tcp
127.0.0.1:8080: connect: connection refused`. Fix in any shell:
`aws eks update-kubeconfig --region $AWS_REGION --name $EKS_CLUSTER_NAME`

---

## 2. WireGuard tunnel — gx10.lan ⇄ AWS, inside the EKS VPC

### 2.1 Find a public subnet in the EKS VPC

The tunnel-server needs a real internet-routable address (via an Internet
Gateway) for inbound WireGuard handshakes from gx10.lan — the private
subnets your nodes live in only have outbound access via a NAT Gateway.

```bash
EKS_VPC_ID=$(aws eks describe-cluster --region $AWS_REGION --name $EKS_CLUSTER_NAME --query 'cluster.resourcesVpcConfig.vpcId' --output text)
aws ec2 describe-route-tables --region $AWS_REGION --filters Name=vpc-id,Values=$EKS_VPC_ID
```

Look through the output for the route table tagged `Name: ...PublicRouteTable`
with a `0.0.0.0/0` route pointing at a `GatewayId` starting `igw-` (not a
`NatGatewayId`) — note one of its associated `SubnetId`s.

```bash
TUNNEL_SUBNET_ID=<the public subnet id you found>
```

### 2.2 Security group + keypair, in the EKS VPC

```bash
TUNNEL_SG_ID=$(aws ec2 create-security-group --region $AWS_REGION --group-name codereview-tunnel-sg --description "WireGuard tunnel server" --vpc-id $EKS_VPC_ID --query 'GroupId' --output text)
echo "SG: $TUNNEL_SG_ID"
```

```bash
# WireGuard's handshake port — fine open to the whole internet; WireGuard's
# security model is the crypto handshake, not source-IP filtering.
aws ec2 authorize-security-group-ingress --region $AWS_REGION --group-id $TUNNEL_SG_ID --protocol udp --port 51820 --cidr 0.0.0.0/0
```

```bash
# SSH for admin access — scoped to your own current IP only.
MY_IP=$(curl -s https://checkip.amazonaws.com)
aws ec2 authorize-security-group-ingress --region $AWS_REGION --group-id $TUNNEL_SG_ID --protocol tcp --port 22 --cidr ${MY_IP}/32
```

```bash
# The LLM port, from pods/nodes — same VPC now, so just the VPC's own CIDR.
aws ec2 authorize-security-group-ingress --region $AWS_REGION --group-id $TUNNEL_SG_ID --protocol tcp --port 8084 --cidr 10.0.0.0/16
```

If you don't already have the SSH keypair from a previous session:

```bash
aws ec2 create-key-pair --region $AWS_REGION --key-name codereview-tunnel-key --query 'KeyMaterial' --output text > ~/.ssh/codereview-tunnel-key.pem
chmod 400 ~/.ssh/codereview-tunnel-key.pem
```

### 2.3 Launch the tunnel-server instance

```bash
AMI_ID=$(aws ssm get-parameters --region $AWS_REGION --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 --query 'Parameters[0].Value' --output text)
echo "AMI: $AMI_ID"
```

```bash
aws ec2 run-instances --region $AWS_REGION --image-id $AMI_ID --instance-type t3.micro --key-name codereview-tunnel-key --subnet-id $TUNNEL_SUBNET_ID --security-group-ids $TUNNEL_SG_ID --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=codereview-tunnel-server}]'
```

**Gotcha**: a long single-line command with `--query`/`--output text` on the
end can get mangled by terminal line-wrapping on paste, silently dropping
the tail of the command (including `--security-group-ids`, causing the
instance to launch with the VPC's `default` SG instead). Prefer no
`--query` here — read `InstanceId` out of the full JSON response yourself:

```bash
TUNNEL_INSTANCE_ID=<paste the InstanceId value>
echo "Instance: $TUNNEL_INSTANCE_ID"
```

**Double-check the security group actually attached** before going further
(this exact failure mode happened once already):

```bash
aws ec2 describe-instances --region $AWS_REGION --instance-ids $TUNNEL_INSTANCE_ID --query 'Reservations[0].Instances[0].SecurityGroups'
```

If it shows `default` instead of `codereview-tunnel-sg`, fix it (this
*replaces* the attached SGs, doesn't add to them):

```bash
aws ec2 modify-instance-attribute --region $AWS_REGION --instance-id $TUNNEL_INSTANCE_ID --groups $TUNNEL_SG_ID
```

Then:

```bash
aws ec2 wait instance-running --region $AWS_REGION --instance-ids $TUNNEL_INSTANCE_ID
aws ec2 modify-instance-attribute --region $AWS_REGION --instance-id $TUNNEL_INSTANCE_ID --no-source-dest-check
```

`no-source-dest-check` is required — this instance will route traffic not
addressed to itself (pod traffic forwarded to gx10.lan's tunnel IP), and AWS
drops such traffic by default as an anti-spoofing precaution.

Elastic IP, so the endpoint address is stable across stop/start/relaunch —
**if you already have one from a previous session, reuse it** (re-associating
just moves it, and means gx10.lan's `Endpoint =` line never needs to
change):

```bash
aws ec2 describe-addresses --region $AWS_REGION   # check for an existing one first

# only if you don't have one yet:
aws ec2 allocate-address --region $AWS_REGION --domain vpc
```

```bash
TUNNEL_EIP_ALLOC=<the AllocationId, new or existing>
aws ec2 associate-address --region $AWS_REGION --instance-id $TUNNEL_INSTANCE_ID --allocation-id $TUNNEL_EIP_ALLOC
aws ec2 describe-addresses --region $AWS_REGION --allocation-ids $TUNNEL_EIP_ALLOC --query 'Addresses[0].PublicIp' --output text
```

**Save the public IP** — you'll need it for gx10.lan's config if this is a
fresh Elastic IP.

### 2.4 WireGuard server config

```bash
ssh -i ~/.ssh/codereview-tunnel-key.pem ec2-user@<TUNNEL_PUBLIC_IP>
```

On the **tunnel-server**. If you saved a private key from a previous
session, skip key generation and just write it directly into the config
below instead of `wg genkey`'s output — reusing it means gx10.lan's
`PublicKey =` line for this peer never needs to change.

```bash
sudo dnf install -y wireguard-tools
wg genkey | sudo tee /etc/wireguard/server_private.key | wg pubkey | sudo tee /etc/wireguard/server_public.key
sudo chmod 600 /etc/wireguard/server_private.key
```

```bash
sudo tee /etc/wireguard/wg0.conf > /dev/null <<EOF
[Interface]
Address = 10.100.0.1/24
ListenPort = 51820
PrivateKey = $(sudo cat /etc/wireguard/server_private.key)
PostUp = sysctl -w net.ipv4.ip_forward=1

[Peer]
PublicKey = <gx10.lan's public key — from a previous session, or generate fresh in Step 2.5 and come back>
AllowedIPs = 10.100.0.2/32
EOF

sudo systemctl enable --now wg-quick@wg0
echo "Server public key (needed for gx10.lan's config):"
sudo cat /etc/wireguard/server_public.key
```

**Gotcha, hit repeatedly**: pasting a multi-line `sudo tee ... <<EOF ...
EOF` heredoc into an interactive SSH session can corrupt the file — a
stray character on the `EOF` line (easy to pick up via copy/paste) stops
bash recognizing it as the closing delimiter, and the literal word `EOF`
ends up written into the config as a line. Symptom: `wg-quick` fails with
`Line unrecognized: 'EOF'`. If any heredoc-written config fails to parse,
don't hand-patch it — `sudo cat -A <file>` first (a stray `^M` or a literal
`EOF` line confirms it), then rewrite the whole file in one clean heredoc.
Also: `/etc/wireguard/` is root-only by design — reading it back always
needs `sudo cat`, plain `cat` failing with `Permission denied` is expected.

### 2.5 WireGuard client config (on gx10.lan)

**On gx10.lan** (not this dev box — your actual GPU machine). If reusing a
previous session's client key, skip `wg genkey` and paste the saved value
into `PrivateKey` instead.

```bash
sudo apt install -y wireguard   # or dnf/pacman, depending on distro
wg genkey | sudo tee /etc/wireguard/client_private.key | wg pubkey | sudo tee /etc/wireguard/client_public.key
sudo chmod 600 /etc/wireguard/client_private.key
```

```bash
sudo tee /etc/wireguard/wg0.conf > /dev/null <<EOF
[Interface]
Address = 10.100.0.2/24
PrivateKey = $(sudo cat /etc/wireguard/client_private.key)

[Peer]
PublicKey = <the tunnel-server's public key from Step 2.4>
Endpoint = <TUNNEL_PUBLIC_IP>:51820
AllowedIPs = 10.100.0.1/32, 10.0.0.0/16
PersistentKeepalive = 25
EOF

sudo systemctl enable --now wg-quick@wg0
echo "Client public key (needed back on the tunnel server if this is a fresh key):"
sudo cat /etc/wireguard/client_public.key
```

Two things baked into this config that weren't obvious the first time
through:

- **`AllowedIPs = 10.100.0.1/32, 10.0.0.0/16`, not just the tunnel-server's
  own address.** `AllowedIPs` is both a routing table *and* a packet filter
  — gx10.lan will only accept decrypted packets whose *source* falls in
  this list. Pod traffic arrives with the pod/node's real IP as source (no
  NAT is applied on the tunnel-server), so without the wider range gx10.lan
  silently drops every request from EKS while the tunnel itself looks
  perfectly healthy (`wg show` shows a happy handshake either way).
- **Putting the wide range directly in the file, not via a live `wg set`.**
  `wg-quick` derives actual OS routes from each `AllowedIPs` entry
  automatically when it parses the config file. Adding the range only via
  live `wg set peer ... allowed-ips ...` updates WireGuard's own internal
  table but *not* the kernel routing table — replies would have nowhere to
  route back through. Both live-only changes (`wg set` and a manual `ip
  route add`) also vanish the next time `wg-quick` restarts for any reason
  (a corrupted-config fix, a reboot), silently reintroducing this exact bug
  — baking it into the file is what makes it durable.

`PersistentKeepalive = 25`: gx10.lan is behind your home router's NAT with
no public IP, so it has to be the one to dial out. Without a periodic
keepalive, the router's NAT mapping for this connection expires and the
server can no longer reach back in, even though the connection still looks
up from gx10.lan's side.

### 2.6 Register the client's key on the server (only if it's a fresh key)

Skip this if you reused a saved client key already baked into the
server's `[Peer]` section in Step 2.4.

**Back on the tunnel-server**:

```bash
sudo tee -a /etc/wireguard/wg0.conf > /dev/null <<EOF

[Peer]
PublicKey = <gx10.lan's public key from Step 2.5>
AllowedIPs = 10.100.0.2/32
EOF
sudo systemctl restart wg-quick@wg0
sudo wg show
```

### 2.7 Verify the tunnel itself

**On gx10.lan**:

```bash
sudo wg show
ping -c3 10.100.0.1
curl http://localhost:8084/v1/models
```

**Back on the tunnel-server**:

```bash
ping -c3 10.100.0.2
curl http://10.100.0.2:8084/v1/models
```

If that last one returns model JSON, the tunnel itself is proven — before
touching pod-to-tunnel routing at all. A `latest handshake:` line in `wg
show` on both sides confirms the crypto layer is fine even before this.

---

## 3. Push the backend image to ECR

The shared image (3 reviewers + api). The dashboard's own image doesn't
exist yet (`Dockerfile.dashboard` is a Phase E task).

```bash
aws ecr create-repository --region $AWS_REGION --repository-name codereview-backend

aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

cd ~/agents/codereview
docker build -t codereview-backend:latest .
docker tag codereview-backend:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-backend:latest
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-backend:latest
```

---

## 4. Route pod traffic to the tunnel-server (same-VPC now — no peering)

Since the tunnel-server lives in the EKS VPC now, this is a completely
standard same-VPC NAT-instance route — one entry per route table, no VPC
peering, no cross-VPC security group scoping.

```bash
EKS_RTB_IDS=$(aws ec2 describe-route-tables --region $AWS_REGION --filters "Name=vpc-id,Values=$EKS_VPC_ID" --query 'RouteTables[].RouteTableId' --output text)
for RTB in $EKS_RTB_IDS; do
  aws ec2 create-route --region $AWS_REGION --route-table-id $RTB --destination-cidr-block 10.100.0.0/24 --instance-id $TUNNEL_INSTANCE_ID
done
```

(Looping over every route table in the VPC, rather than guessing which
subnets nodes/pods landed in, is deliberate — correct regardless of
eksctl's actual subnet placement. If a route already exists for
`10.100.0.0/24` on any of them — e.g. you're redoing this after relaunching
the tunnel-server instance — use `aws ec2 replace-route` instead of
`create-route` for that one, since `create-route` errors on a CIDR that's
already routed rather than updating it.)

Verify:

```bash
kubectl run curltest --rm -it --restart=Never --image=curlimages/curl -- curl -sS http://10.100.0.2:8084/v1/models
```

JSON model list back = a pod, inside EKS, reached your home GPU box through
same-VPC routing → tunnel-server → WireGuard → your home network.

---

## 5. Install the AWS Load Balancer Controller — done 2026-09-02

Needed so Phase E's Ingress resource actually provisions an ALB. IRSA (IAM
Roles for Service Accounts): the controller's pod assumes an IAM role via
its Kubernetes ServiceAccount, not a static AWS key baked into the pod.

```bash
eksctl utils associate-iam-oidc-provider --region $AWS_REGION --cluster $EKS_CLUSTER_NAME --approve

curl -o iam-policy.json https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/main/docs/install/iam_policy.json

aws iam create-policy --policy-name AWSLoadBalancerControllerIAMPolicy --policy-document file://iam-policy.json

eksctl create iamserviceaccount \
  --cluster $EKS_CLUSTER_NAME --region $AWS_REGION \
  --namespace kube-system --name aws-load-balancer-controller \
  --attach-policy-arn arn:aws:iam::$AWS_ACCOUNT_ID:policy/AWSLoadBalancerControllerIAMPolicy \
  --approve

helm repo add eks https://aws.github.io/eks-charts
helm repo update

helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=$EKS_CLUSTER_NAME \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller \
  --set region=$AWS_REGION \
  --set vpcId=$EKS_VPC_ID

kubectl rollout status -n kube-system deployment/aws-load-balancer-controller
```

---

## Where this leaves us

Done: cluster up, 2 nodes ready, backend image in ECR, WireGuard tunnel
proven pod-to-gx10.lan (same-VPC routing, no peering), ALB controller
running. Not yet: any app pods, an Ingress, or the dashboard image — Phase
E, next session.

---

## Debugging toolkit (if the pod-to-tunnel path ever breaks again)

In rough order of how deep into the stack each one looks:

- `aws ec2 describe-route-tables` / `describe-security-groups` /
  `describe-network-acls` — full JSON, not narrow `--query` filters, when
  actually debugging (a filtered query can hide the one field that matters).
- A **long-lived debug pod** beats one-shot `kubectl run --rm` pods for
  anything beyond a single request: `kubectl run netshoot --image=nicolaka/netshoot --restart=Never -- sleep 7200`,
  then `kubectl exec -it netshoot -- bash` to poke around (`ip route`,
  `curl -v`, `ping`) repeatedly without recreating it each time.
- `kubectl debug node/<node-name> -it --image=nicolaka/netshoot` — a shell
  that shares the **node's own** network namespace (not a pod's), for
  `tcpdump -i any -nn 'host <dest>'` to see traffic exactly as it leaves the
  node, including the real (possibly SNAT'd) source IP the VPC CNI applies
  for any destination outside the VPC's own CIDR.
- `tcpdump` on the tunnel-server itself: use `-i <real-interface-name>`
  (e.g. `ens5`), not `-i any` — the Linux "cooked" pseudo-interface has
  shown a reproducible bug in this project where it reports "1 packet
  received by filter" but displays 0, even with `-l` for unbuffered output.
- `curl -v` beats a plain `curl` for any hang — "Connection timed out"
  before ever printing `Connected to...` means the network never delivered
  a reply at all (routing/firewall territory); "Connection refused" means
  delivery worked and *nothing is listening* on the far end (an application
  problem, not a networking one) — very different debugging paths.

---

## Teardown (between sessions)

```bash
eksctl delete cluster --name $EKS_CLUSTER_NAME --region $AWS_REGION
```

This now deletes the tunnel-server too (it lives in the cluster's VPC).
**Before running this**, save the values Step 2 calls out — the WireGuard
server and client private keys, both public keys, and the Elastic IP's
`AllocationId` — somewhere outside the VPC (a text note is enough). Next
session, Step 2 becomes "relaunch + paste the same keys back in" rather
than "regenerate everything and reconfigure gx10.lan again."

See `deploy/teardown.sh` for this wrapped as a script (update it to match —
it currently still assumes the old peering-connection design).
