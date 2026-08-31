import boto3
import json
import uuid
from decimal import Decimal
from datetime import datetime
from urllib.parse import unquote_plus


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError

s3 = boto3.client('s3')
rekognition = boto3.client('rekognition')
dynamodb = boto3.resource('dynamodb')
sns = boto3.client('sns')

TABLE_NAME = "ImageResults"
SNS_TOPIC_ARN = "arn:aws:sns:ap-south-1:839404219295:image-moderation-alerts"
MODERATION_THRESHOLD = 0.0

def lambda_handler(event, context):
    table = dynamodb.Table(TABLE_NAME)

    for record in event['Records']:
        bucket = record['s3']['bucket']['name']
        key = unquote_plus(record['s3']['object']['key'])

        if not key.startswith("incoming/"):
            continue

        image_id = str(uuid.uuid4())

        # 1. Detect labels (auto-tagging)
        label_response = rekognition.detect_labels(
            Image={'S3Object': {'Bucket': bucket, 'Name': key}},
            MaxLabels=10,
            MinConfidence=70
        )
        labels = [
            {"name": l['Name'], "confidence": Decimal(str(round(l['Confidence'], 2)))}
            for l in label_response['Labels']
        ]

        # 2. Detect moderation labels (content safety)
        moderation_response = rekognition.detect_moderation_labels(
            Image={'S3Object': {'Bucket': bucket, 'Name': key}},
            MinConfidence=1
        )
        moderation_labels = [
            {"name": m['Name'], "confidence": Decimal(str(round(m['Confidence'], 2)))}
            for m in moderation_response['ModerationLabels']
        ]

        is_flagged = any(
             m['confidence'] >= Decimal(str(MODERATION_THRESHOLD)) for m in moderation_labels
        )
        # 3. Store results in DynamoDB
        table.put_item(Item={
            "imageId": image_id,
            "originalKey": key,
            "bucket": bucket,
            "labels": labels,
            "moderationLabels": moderation_labels,
            "flagged": is_flagged,
            "timestamp": datetime.utcnow().isoformat()
        })

        # 4. If flagged, move to quarantine + send SNS alert
        if is_flagged:
            new_key = key.replace("incoming/", "quarantine/")
            s3.copy_object(
                Bucket=bucket,
                CopySource={'Bucket': bucket, 'Key': key},
                Key=new_key
            )
            s3.delete_object(Bucket=bucket, Key=key)

            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject="Flagged Image Detected",
                Message=f"Image {key} was flagged and moved to quarantine.\n"
                        f"Labels: {json.dumps(moderation_labels, indent=2, default=decimal_default)}"
            )

        print(f"Processed {key} -> imageId {image_id}, flagged={is_flagged}")

    return {"statusCode": 200, "body": "Processing complete"}
