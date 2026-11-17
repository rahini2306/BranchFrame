terraform {
  required_version = ">= 1.9.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "BranchFrame", Managed = "Terraform" }
  }
}
variable "region" {
  type    = string
  default = "ap-south-1"
}
variable "bucket_name" {
  type        = string
  description = "Globally unique private media bucket name"
}
variable "image_tag" {
  type        = string
  description = "Immutable ECR image tag, normally the source commit SHA"
}
variable "certificate_arn" {
  type        = string
  description = "Validated ACM certificate in this region for your demo domain"
}
variable "allowed_cidr" {
  type        = string
  description = "Your public IPv4 /32 for a restricted demo"
  validation {
    condition     = can(cidrnetmask(var.allowed_cidr))
    error_message = "Provide an IPv4 CIDR."
  }
}
variable "worker_token_parameter_arn" {
  type        = string
  description = "Existing SSM SecureString ARN, encrypted with the default SSM key. Create outside Terraform to keep the secret out of state."
}
variable "worker_count" {
  type    = number
  default = 1
  validation {
    condition     = var.worker_count >= 1 && var.worker_count <= 8 && floor(var.worker_count) == var.worker_count
    error_message = "Use 1 to 8 workers."
  }
}
data "aws_vpc" "default" {
  default = true
}
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}
data "aws_ssm_parameter" "ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}
resource "aws_ecr_repository" "app" {
  name                 = "branchframe"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}
resource "aws_s3_bucket" "media" {
  bucket        = var.bucket_name
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "media" {
  bucket = aws_s3_bucket.media.id
  versioning_configuration {
    status = "Enabled"
  }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_policy" "tls" {
  bucket = aws_s3_bucket.media.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.media.arn, "${aws_s3_bucket.media.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}
resource "aws_cloudwatch_log_group" "app" {
  name              = "/branchframe/app"
  retention_in_days = 7
}
resource "aws_cloudwatch_log_group" "worker" {
  name              = "/branchframe/worker"
  retention_in_days = 7
}
resource "aws_security_group" "alb" {
  name_prefix = "bf-alb-"
  vpc_id      = data.aws_vpc.default.id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
  }
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_security_group" "worker" {
  name_prefix = "bf-worker-"
  vpc_id      = data.aws_vpc.default.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_security_group" "api" {
  name_prefix = "bf-api-"
  vpc_id      = data.aws_vpc.default.id
  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id, aws_security_group.worker.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_iam_role" "api" {
  name_prefix = "bf-api-"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }]
  })
}
resource "aws_iam_instance_profile" "api" {
  role = aws_iam_role.api.name
}
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "api" {
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.media.arn}/*" },
      { Effect = "Allow", Action = "s3:ListBucket", Resource = aws_s3_bucket.media.arn },
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = aws_ecr_repository.app.arn },
      { Effect = "Allow", Action = "ssm:GetParameter", Resource = var.worker_token_parameter_arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.app.arn}:*" }
    ]
  })
}
resource "aws_instance" "api" {
  ami                         = data.aws_ssm_parameter.ami.value
  instance_type               = "t3.small"
  subnet_id                   = sort(data.aws_subnets.default.ids)[0]
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.api.id]
  iam_instance_profile        = aws_iam_instance_profile.api.name
  # Update the image through SSM; replacing the host requires metadata restoration.
  user_data_replace_on_change = false
  user_data = templatefile("${path.module}/bootstrap.sh.tftpl", {
    region    = var.region, bucket = aws_s3_bucket.media.id
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    registry  = split("/", aws_ecr_repository.app.repository_url)[0]
    token_arn = var.worker_token_parameter_arn
    log_group = aws_cloudwatch_log_group.app.name
  })
  root_block_device {
    volume_size           = 30
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = false
  }
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  tags       = { Name = "BranchFrame API" }
  depends_on = [aws_iam_role_policy.api]
}
resource "aws_lb" "app" {
  name_prefix        = "bf-"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.aws_subnets.default.ids
  idle_timeout       = 120
}
resource "aws_lb_target_group" "api" {
  name_prefix = "bf-"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  health_check {
    path = "/api/health"
  }
}
resource "aws_lb_target_group_attachment" "api" {
  target_group_arn = aws_lb_target_group.api.arn
  target_id        = aws_instance.api.id
  port             = 8000
}
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}
resource "aws_ecs_cluster" "render" {
  name = "branchframe"
}
resource "aws_iam_role" "execution" {
  name_prefix = "bf-execution-"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ecs-tasks.amazonaws.com" } }]
  })
}
resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
resource "aws_iam_role_policy" "execution_secret" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "ssm:GetParameters", Resource = var.worker_token_parameter_arn }]
  })
}
resource "aws_iam_role" "worker" {
  name_prefix        = "bf-worker-"
  assume_role_policy = aws_iam_role.execution.assume_role_policy
}
resource "aws_iam_role_policy" "worker" {
  role = aws_iam_role.worker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "s3:GetObject", Resource = ["${aws_s3_bucket.media.arn}/assets/*", "${aws_s3_bucket.media.arn}/segments/*"] },
      { Effect = "Allow", Action = "s3:PutObject", Resource = ["${aws_s3_bucket.media.arn}/segments/*", "${aws_s3_bucket.media.arn}/exports/*"] }
    ]
  })
}
resource "aws_ecs_task_definition" "worker" {
  family                   = "branchframe-render"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.worker.arn
  ephemeral_storage {
    size_in_gib = 30
  }
  container_definitions = jsonencode([{
    name        = "worker", image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}", essential = true
    command     = ["python", "-m", "branchframe.worker", "--api", "http://${aws_instance.api.private_ip}:8000"]
    environment = [{ name = "BRANCHFRAME_BUCKET", value = aws_s3_bucket.media.id }, { name = "AWS_DEFAULT_REGION", value = var.region }]
    secrets     = [{ name = "BRANCHFRAME_WORKER_TOKEN", valueFrom = var.worker_token_parameter_arn }]
    healthCheck = { command = ["CMD", "python", "-c", "import os; assert os.environ.get('BRANCHFRAME_BUCKET')"], interval = 60, timeout = 5, retries = 3 }
    logConfiguration = {
      logDriver = "awslogs"
      options   = { awslogs-group = aws_cloudwatch_log_group.worker.name, awslogs-region = var.region, awslogs-stream-prefix = "render" }
    }
  }])
  depends_on = [aws_iam_role_policy.execution_secret]
}
resource "aws_ecs_service" "worker" {
  name            = "branchframe-render"
  cluster         = aws_ecs_cluster.render.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_count
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = true
  }
}
output "repository_url" {
  value = aws_ecr_repository.app.repository_url
}
output "load_balancer_dns" {
  value = aws_lb.app.dns_name
}
output "api_instance" {
  value = aws_instance.api.id
}
output "retained_metadata_volume" {
  value = aws_instance.api.root_block_device[0].volume_id
}
