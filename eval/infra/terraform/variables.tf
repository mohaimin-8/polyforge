variable "hcloud_token" {
  description = "Hetzner Cloud API token (export TF_VAR_hcloud_token=...; never commit it)"
  type        = string
  sensitive   = true
}

variable "cluster_name" {
  description = "Prefix for every resource; one name = one disposable cluster"
  type        = string
  default     = "polyforge-eval"
}

variable "server_type" {
  description = "Hetzner server type for all nodes (cx32 = 4 vCPU / 8 GB)"
  type        = string
  default     = "cx32"
}

variable "worker_count" {
  description = "Worker nodes. 3 = the 4-node W35a baseline; raise to 5 if W35b falls >20% behind schedule (still under budget)"
  type        = number
  default     = 3
}

variable "location" {
  description = "Hetzner location"
  type        = string
  default     = "nbg1"
}

variable "network_zone" {
  description = "Hetzner network zone matching the location"
  type        = string
  default     = "eu-central"
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key used for node access"
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "admin_cidrs" {
  description = "CIDRs allowed to reach SSH and the k3s API (set to your IP/32)"
  type        = list(string)
}
