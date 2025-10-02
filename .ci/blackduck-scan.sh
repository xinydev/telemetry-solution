#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

BASELINE_VERSION=$1
CURRENT_VERSION=$2
PROJECT_ID=$3
PROJECT_NAME=$4
SIGNATURE_SCAN_PATHS=$5

echo BASELINE_VERSION:$BASELINE_VERSION
echo CURRENT_VERSION:$CURRENT_VERSION

python -m pip freeze > requirements.txt
curl -s -L $BLACKDUCK_DETECT_SCRIPT_URL -o detect_arm.sh
chmod +x ./detect_arm.sh
./detect_arm.sh \
    --blackduck.url=$BLACKDUCK_HOST_URL \
    --detect.project.name=$PROJECT_NAME \
    --blackduck.api.token=$BLACKDUCK_SVC_ACCOUNT_API_KEY \
    --detect.project.version.name=$CURRENT_VERSION \
    --detect.accuracy.required=NONE \
    --detect.tools=DETECTOR \
    --detect.source.path=. \
    --detect.python.pip.requirements.file=requirements.txt

export BDS_JAVA_HOME=/usr/lib/jvm/java-21-openjdk-arm64/
./detect_arm.sh \
      --blackduck.url=$BLACKDUCK_HOST_URL \
      --detect.project.name=$PROJECT_NAME \
      --blackduck.api.token=$BLACKDUCK_SVC_ACCOUNT_API_KEY \
      --detect.project.version.name=$CURRENT_VERSION \
      --detect.source.path=. \
      --blackduck.trust.cert=true \
      --detect.cleanup=false \
      --detect.project.tags=snippet_scan \
      --detect.tools=SIGNATURE_SCAN \
      --detect.blackduck.signature.scanner.individual.file.matching=ALL \
      --detect.detector.search.continue=true \
      --detect.detector.search.depth=05 \
      --detect.impact.analysis.enabled=true \
      --detect.binary.scan.search.depth=05 \
      --detect.blackduck.scan.mode=INTELLIGENT \
      --detect.blackduck.signature.scanner.copyright.search=true \
      --detect.blackduck.signature.scanner.license.search=true \
      --detect.blackduck.signature.scanner.snippet.matching=SNIPPET_MATCHING \
      --detect.blackduck.signature.scanner.paths=$SIGNATURE_SCAN_PATHS \
      --detect.blackduck.signature.scanner.upload.source.mode=true

blackduck_get_json() {
    local bearer_token=$1
    local link_to_download=$2

    curl -s -X GET --header "Content-Type:application/json" --header "Authorization: bearer $bearer_token" $link_to_download
}

get_snippets() {
    local bearer_token=$1
    local version=$2

    local version_link=$(blackduck_get_json $bearer_token $BLACKDUCK_HOST_URL'/api/projects/'$PROJECT_ID'/versions/?limit=1000' | jq -r '.items[] | select(.versionName=="'$version'") | ._meta.href ')
    local link=$version_link/'matched-files?limit=1000&filter=bomMatchType%3Asnippet'

    local final_res=""
    local res=$(blackduck_get_json $bearer_token $link | jq -r '.items[] | "\(.matches[].snippet)|\(.uri)"' | grep -o 'nodes.*')
    for i in $res;do
        final_res+="${i}|$version_link"'/components?limit=100&offset=0 '
    done
    echo $final_res
}

get_components_link() {
    local bearer_token=$1
    local version=$2

    blackduck_get_json $bearer_token $BLACKDUCK_HOST_URL'/api/projects/'$PROJECT_ID'/versions/?limit=1000' | jq -r '.items[] | select(.versionName=="'$version'") | ._meta.links[] | select(.rel=="components") | .href '
}

get_risk() {
    local bearer_token=$1
    local components_link=$2
    local risk_type=$3
    local risk_level=$4

    local risk_uri=$components_link/'?filter=bomInclusion%3Afalse&filter=bomMatchInclusion%3Afalse&filter=bomMatchReviewStatus%3Areviewed&filter='$risk_type'Risk%3A'$risk_level'&limit=1000'

    blackduck_get_json $bearer_token $risk_uri | jq -r '.items[] | "\(.componentName)|\(._meta.href)"'
}

BEARER_TOKEN=$(curl -s -X POST -H "Accept: application/vnd.blackducksoftware.user-4+json" -H "Content-Type: application/json" -H "Authorization: token $BLACKDUCK_SVC_ACCOUNT_API_KEY" ${BLACKDUCK_HOST_URL}/api/tokens/authenticate | jq -r '.bearerToken')

BASELINE_COMPONENTS_LINK=$(get_components_link $BEARER_TOKEN $BASELINE_VERSION)

BASELINE_OPERATIONAL_MEDIUM=$(get_risk $BEARER_TOKEN $BASELINE_COMPONENTS_LINK operational MEDIUM)
BASELINE_OPERATIONAL_HIGH=$(get_risk $BEARER_TOKEN $BASELINE_COMPONENTS_LINK operational HIGH)

BASELINE_LICENSE_MEDIUM=$(get_risk $BEARER_TOKEN $BASELINE_COMPONENTS_LINK license MEDIUM)
BASELINE_LICENSE_HIGH=$(get_risk $BEARER_TOKEN $BASELINE_COMPONENTS_LINK license HIGH)

BASELINE_SECURITY_MEDIUM=$(get_risk $BEARER_TOKEN $BASELINE_COMPONENTS_LINK security MEDIUM)
BASELINE_SECURITY_HIGH=$(get_risk $BEARER_TOKEN $BASELINE_COMPONENTS_LINK security HIGH)
BASELINE_SECURITY_CRITICAL=$(get_risk $BEARER_TOKEN $BASELINE_COMPONENTS_LINK security CRITICAL)



CURRENT_COMPONENTS_LINK=$(get_components_link $BEARER_TOKEN $CURRENT_VERSION)

CURRENT_OPERATIONAL_MEDIUM=$(get_risk $BEARER_TOKEN $CURRENT_COMPONENTS_LINK operational MEDIUM)
CURRENT_OPERATIONAL_HIGH=$(get_risk $BEARER_TOKEN $CURRENT_COMPONENTS_LINK operational HIGH)

CURRENT_LICENSE_MEDIUM=$(get_risk $BEARER_TOKEN $CURRENT_COMPONENTS_LINK license MEDIUM)
CURRENT_LICENSE_HIGH=$(get_risk $BEARER_TOKEN $CURRENT_COMPONENTS_LINK license HIGH)

CURRENT_SECURITY_MEDIUM=$(get_risk $BEARER_TOKEN $CURRENT_COMPONENTS_LINK security MEDIUM)
CURRENT_SECURITY_HIGH=$(get_risk $BEARER_TOKEN $CURRENT_COMPONENTS_LINK security HIGH)
CURRENT_SECURITY_CRITICAL=$(get_risk $BEARER_TOKEN $CURRENT_COMPONENTS_LINK security CRITICAL)

OVERALL_RESULT=0

check_for_new_items() {
    local item_type=$1
    local items_to_check=$2
    local items_to_check_against=$3

    local arr1=($items_to_check)
    local arr2=($items_to_check_against)

    local arr2_stripped=("${arr2[@]/|*/}")

    echo "${arr1[@]}"
    echo "${arr2_stripped[@]}"

    for item in "${arr1[@]}"; do
        local item_stripped="${item%%|*}"
        if [[ ! " ${arr2_stripped[@]} " =~ " ${item_stripped} " ]]; then
            echo "New $item_type finding: "
            echo "$item" | tr '|' '\n'
            echo
            OVERALL_RESULT=1
        fi
    done
}

check_for_new_items "operational HIGH risk" "$CURRENT_OPERATIONAL_HIGH" "$BASELINE_OPERATIONAL_HIGH"
check_for_new_items "operational MEDIUM risk" "$CURRENT_OPERATIONAL_MEDIUM" "$BASELINE_OPERATIONAL_HIGH $BASELINE_OPERATIONAL_MEDIUM"
check_for_new_items "license HIGH risk" "$CURRENT_LICENSE_HIGH" "$BASELINE_LICENSE_HIGH"
check_for_new_items "license MEDIUM risk" "$CURRENT_LICENSE_MEDIUM" "$BASELINE_LICENSE_HIGH $BASELINE_LICENSE_MEDIUM"
check_for_new_items "security CRITICAL risk" "$CURRENT_SECURITY_CRITICAL" "$BASELINE_SECURITY_CRITICAL"
check_for_new_items "security HIGH risk" "$CURRENT_SECURITY_HIGH" "$BASELINE_SECURITY_CRITICAL $BASELINE_SECURITY_HIGH"
check_for_new_items "security MEDIUM risk" "$CURRENT_SECURITY_MEDIUM" "$BASELINE_SECURITY_CRITICAL $BASELINE_SECURITY_HIGH $BASELINE_SECURITY_MEDIUM"
CURRENT_SNIPPETS=$(get_snippets $BEARER_TOKEN $CURRENT_VERSION)
BASELINE_SNIPPETS=$(get_snippets $BEARER_TOKEN $BASELINE_VERSION)

check_for_new_items "snippet match" "$CURRENT_SNIPPETS" "$BASELINE_SNIPPETS"

if [ "$OVERALL_RESULT" -eq 0 ];then
    echo "No new findings"
    echo "SUCCESS"
else
    echo "New findings found"
    echo "FAILURE"
fi

exit $OVERALL_RESULT
