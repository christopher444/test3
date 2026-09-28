variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "name" {
  type    = string
  default = "catalogue-sync"
}

variable "container_image" {
  type        = string
  description = "Immutable ECR image URI, preferably with digest"
}

variable "product_api_url" {
  type = string
}

variable "warehouse_api_url" {
  type = string
}

variable "schedule_expression" {
  type    = string
  default = "cron(0 5 * * ? *)"
}

variable "schedule_timezone" {
  type    = string
  default = "UTC"
}

variable "tags" {
  type    = map(string)
  default = {}
}
