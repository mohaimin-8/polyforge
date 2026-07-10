output "control_ip" {
  description = "Public IP of the k3s control node (kubeconfig: scp root@IP:/etc/rancher/k3s/k3s.yaml)"
  value       = hcloud_server.control.ipv4_address
}

output "worker_ips" {
  description = "Public IPs of the worker nodes"
  value       = hcloud_server.worker[*].ipv4_address
}

output "kubeconfig_hint" {
  value = "scp root@${hcloud_server.control.ipv4_address}:/etc/rancher/k3s/k3s.yaml kubeconfig.yaml && sed -i 's/127.0.0.1/${hcloud_server.control.ipv4_address}/' kubeconfig.yaml"
}
