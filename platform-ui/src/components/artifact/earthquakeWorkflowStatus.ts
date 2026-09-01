export const isVerifiedAndAccepted = (status: any) =>
  Boolean(status?.all_verified_execution) && Boolean(status?.all_accepted);

export const verificationLabel = (status: any) =>
  `${status?.all_verified_execution ? "verified" : "unverified"} / ${status?.all_accepted ? "accepted" : "not accepted"}`;

export const workflowAcceptanceLabel = (
  validationStatus: any,
  reviewStatus: any,
  finalRecommendationStatus?: string | null
) => {
  if (finalRecommendationStatus === "ACCEPTED") return "最终推荐方案";
  if (finalRecommendationStatus === "DIAGNOSTIC_NOT_ACCEPTED") return "诊断结果/未接受";
  return isVerifiedAndAccepted(validationStatus) && isVerifiedAndAccepted(reviewStatus)
    ? "最终推荐方案"
    : "诊断结果/未接受";
};
