terraform {
  required_version = ">= 1.8.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_bigquery_dataset" "raw_revops" {
  dataset_id                 = "raw_revops"
  friendly_name              = "Raw RevOps Control Plane"
  description                = "Replicated source, canonical, conflict, and run audit tables."
  location                   = var.bigquery_location
  delete_contents_on_destroy = false
}

resource "google_bigquery_dataset" "analytics_revops" {
  dataset_id                 = "analytics_revops"
  friendly_name              = "RevOps Analytics"
  description                = "dbt-managed canonical account and sync-quality models."
  location                   = var.bigquery_location
  delete_contents_on_destroy = false
}
