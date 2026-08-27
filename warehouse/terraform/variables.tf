variable "project_id" {
  description = "Existing GCP project ID with billing and BigQuery API enabled."
  type        = string
}

variable "region" {
  description = "Default Google provider region."
  type        = string
  default     = "asia-south1"
}

variable "bigquery_location" {
  description = "BigQuery dataset location."
  type        = string
  default     = "asia-south1"
}
