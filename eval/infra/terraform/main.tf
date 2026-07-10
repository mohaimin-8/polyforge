# W35a experiment cluster: reproducible 4-node Hetzner Cloud cluster,
# provisioned from scratch with `terraform apply`, destroyed with
# `terraform destroy` after each experiment batch. k3s over cloud-init:
# the harness's cluster backend talks to any conformant kube-apiserver,
# and k3s boots in ~30 s on CX-class instances.
#
# Budget check (roadmap: full eval <= $200): 4 x cx32 at ~EUR 0.0113/h
# ~= EUR 1.09/day for the fleet; 7 days of W35b wall time ~= EUR 8 of
# compute plus traffic — an order of magnitude under budget even with
# the 6-node scale-out contingency.

terraform {
  required_version = ">= 1.7"
  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.45"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "hcloud" {
  token = var.hcloud_token
}

resource "hcloud_ssh_key" "eval" {
  name       = "${var.cluster_name}-key"
  public_key = file(var.ssh_public_key_path)
}

resource "hcloud_network" "eval" {
  name     = var.cluster_name
  ip_range = "10.42.0.0/16"
}

resource "hcloud_network_subnet" "eval" {
  network_id   = hcloud_network.eval.id
  type         = "cloud"
  network_zone = var.network_zone
  ip_range     = "10.42.1.0/24"
}

resource "hcloud_firewall" "eval" {
  name = var.cluster_name

  # SSH and the k3s API from the operator's IP only; everything else stays
  # on the private network.
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = var.admin_cidrs
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "6443"
    source_ips = var.admin_cidrs
  }
}

resource "hcloud_server" "control" {
  name         = "${var.cluster_name}-control"
  server_type  = var.server_type
  image        = "ubuntu-24.04"
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.eval.id]
  firewall_ids = [hcloud_firewall.eval.id]

  network {
    network_id = hcloud_network.eval.id
    ip         = "10.42.1.10"
  }

  user_data = templatefile("${path.module}/cloud-init-control.yaml.tftpl", {
    k3s_token = random_password.k3s_token.result
  })

  depends_on = [hcloud_network_subnet.eval]
}

resource "hcloud_server" "worker" {
  count        = var.worker_count
  name         = "${var.cluster_name}-worker-${count.index}"
  server_type  = var.server_type
  image        = "ubuntu-24.04"
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.eval.id]
  firewall_ids = [hcloud_firewall.eval.id]

  network {
    network_id = hcloud_network.eval.id
    ip         = "10.42.1.2${count.index}"
  }

  user_data = templatefile("${path.module}/cloud-init-worker.yaml.tftpl", {
    k3s_token  = random_password.k3s_token.result
    control_ip = "10.42.1.10"
  })

  depends_on = [hcloud_server.control]
}

resource "random_password" "k3s_token" {
  length  = 48
  special = false
}
