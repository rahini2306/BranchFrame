# A generated HTTPS hostname avoids requiring a purchased domain/certificate.
# The origin header is sensitive and lives in private Terraform state.
resource "random_password" "origin" {
  length  = 48
  special = false
}
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}
data "aws_cloudfront_origin_request_policy" "viewer" {
  name = "Managed-AllViewerAndCloudFrontHeaders-2022-06"
}
resource "aws_cloudfront_vpc_origin" "app" {
  vpc_origin_endpoint_config {
    name                   = "branchframe-private-alb"
    arn                    = aws_lb.app.arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }
  depends_on = [aws_lb_listener_rule.cloudfront]
}
resource "aws_cloudfront_distribution" "app" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "BranchFrame authenticated application"
  price_class     = "PriceClass_100"
  origin {
    domain_name = aws_lb.app.dns_name
    origin_id   = "branchframe-alb"
    custom_header {
      name  = "X-BranchFrame-Origin"
      value = random_password.origin.result
    }
    vpc_origin_config {
      vpc_origin_id       = aws_cloudfront_vpc_origin.app.id
      origin_read_timeout = 60
    }
  }
  default_cache_behavior {
    target_origin_id         = "branchframe-alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.viewer.id
    compress                 = false
  }
  custom_error_response {
    error_code            = 403
    error_caching_min_ttl = 0
  }
  custom_error_response {
    error_code            = 404
    error_caching_min_ttl = 0
  }
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
  viewer_certificate {
    cloudfront_default_certificate = true
  }
  depends_on = [aws_lb_listener_rule.cloudfront]
}
resource "aws_budgets_budget" "project" {
  name         = "BranchFrame-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  # Account-wide: new accounts may not have activated project cost tags yet.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
resource "aws_sns_topic" "alerts" {
  name = "branchframe-operations"
}
resource "aws_sns_topic_subscription" "alerts" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
resource "aws_cloudwatch_metric_alarm" "api_unhealthy" {
  alarm_name          = "BranchFrame-API-unhealthy"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1
  treat_missing_data  = "breaching"
  dimensions = {
    LoadBalancer = aws_lb.app.arn_suffix
    TargetGroup  = aws_lb_target_group.api.arn_suffix
  }
  alarm_actions = [aws_sns_topic.alerts.arn]
}
output "website_url" {
  value = "https://${aws_cloudfront_distribution.app.domain_name}"
}
