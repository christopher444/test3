variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "endpoint" {
  type    = string
  default = "http://127.0.0.1:4566"
}

variable "prefix" {
  type    = string
  default = "catalogue-sync"
}

variable "local_container_image" {
  description = "Local Docker image registered in the ECS task definition. Terraform does not run it automatically."
  type        = string
  default     = "catalogue-sync-local:latest"
}
