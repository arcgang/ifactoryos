# immutable-audit-log — Azure Blob Storage (WORM/Object Lock), AES-256 encryption
# LLD component cmp:immutable-audit-log. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.

variable "environment" {
  type = string
}
