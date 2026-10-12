terraform {
  required_version = ">= 1.6"
}

variable "environment" {
  type    = string
  default = "dev"
}
